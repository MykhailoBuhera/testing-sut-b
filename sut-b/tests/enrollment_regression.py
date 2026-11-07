import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple
from uuid import uuid4

import psycopg
import pytest
from testcontainers.postgres import PostgresContainer

from courses.models import Student, Course, EnrollmentRequest, EnrollmentStatus as S
from courses.repositories import PostgresStudentRepository, PostgresCourseRepository, PostgresEnrollmentRepository
from courses.service import EnrollmentService, EnrollmentRejectedError


# Усі тести модуля входять у повний regression suite; smoke-тести додатково мають @pytest.mark.smoke.
pytestmark = pytest.mark.regression

POLICY_DELAY_S = float(os.getenv("REGRESSION_POLICY_DELAY_MS", "200")) / 1000
# Верхня межа очікування, а не синхронізація: тест завершиться одразу, щойно операція виконається.
OPERATION_TIMEOUT_S = POLICY_DELAY_S + 10


class Policy:
    def is_allowed(self, student, course):
        return True


class Sut(NamedTuple):
    db: psycopg.Connection
    students: PostgresStudentRepository
    courses: PostgresCourseRepository
    enrollments: PostgresEnrollmentRepository
    service: EnrollmentService


@pytest.fixture(scope="module")
def sut():
    with PostgresContainer("postgres:17.6-alpine") as postgres:
        with psycopg.connect(host=postgres.get_container_host_ip(), port=postgres.get_exposed_port(5432),
                             user=postgres.username, password=postgres.password,
                             dbname=postgres.dbname, autocommit=True) as db:
            migration = Path(__file__).resolve().parents[1] / "migrations/V1__create_tables.sql"
            db.execute(migration.read_text(encoding="utf-8"))
            students = PostgresStudentRepository(db)
            courses = PostgresCourseRepository(db)
            enrollments = PostgresEnrollmentRepository(db)
            service = EnrollmentService(students, courses, enrollments, Policy())
            yield Sut(db, students, courses, enrollments, service)


@pytest.fixture(autouse=True)
def clean_database(sut):
    # Кожен тест стартує з порожніх таблиць, незалежно від того, що й у якому порядку виконувалось раніше.
    sut.db.execute("TRUNCATE enrollments, students, courses CASCADE")
    yield


def unique_code():
    return str(uuid4())


def student(sut, age=20, entry_score=90, regular=False):
    return sut.students.create(Student(0, unique_code(), age, entry_score, regular))


def course(sut):
    return sut.courses.create(Course(0, unique_code(), 100000, 10, 1))


def request(person, training):
    return EnrollmentRequest(unique_code(), person.id, training.id, False)


@pytest.mark.smoke
def test_create_enrollment(sut):
    saved = sut.service.enroll(request(student(sut), course(sut)))
    assert saved.status == S.ENROLLED
    assert sut.enrollments.find_by_id(saved.id) == saved


@pytest.mark.smoke
def test_cancel_enrollment(sut):
    enrolled = sut.service.enroll(request(student(sut), course(sut)))
    cancelled = sut.service.cancel(enrolled.id, 7)
    assert cancelled.status == S.CANCELLED
    assert cancelled.refund == 100000
    assert sut.enrollments.find_by_id(enrolled.id) == cancelled


def test_student_age(sut):
    saved = student(sut, age=20, entry_score=90, regular=False)
    assert sut.students.find_by_id(saved.id).age == 20


def test_student_score(sut):
    saved = student(sut, age=25, entry_score=85, regular=True)
    assert sut.students.find_by_id(saved.id).entry_score == 85


def test_enrollment_after_policy_check(sut):
    class DelayedPolicy:
        def is_allowed(self, person, training):
            # Імітація повільної зовнішньої залежності; затримка в тесті не використовується як синхронізація.
            time.sleep(POLICY_DELAY_S)
            return True

    training = course(sut)
    data = request(student(sut), training)
    service = EnrollmentService(sut.students, sut.courses, sut.enrollments, DelayedPolicy())
    with ThreadPoolExecutor(max_workers=1) as executor:
        operation = executor.submit(service.enroll, data)
        # Condition-based wait: чекаємо саме завершення enroll, а не фіксовані 50 мс.
        result = operation.result(timeout=OPERATION_TIMEOUT_S)
    saved = sut.enrollments.find_enrollments(training.id, S.ENROLLED)
    assert saved == [result]
    assert saved[0].reference == data.reference
    assert saved[0].total == 100000


def test_enrollment_selection(sut):
    training = course(sut)
    prefix = unique_code()[:8]
    references = [prefix + "-A", prefix + "-B"]
    fixture_order = list(reversed(references)) if os.getenv("REGRESSION_REVERSE_FIXTURE") == "true" else references
    for reference in fixture_order:
        sut.service.enroll(EnrollmentRequest(reference, student(sut).id, training.id, False))
    # Порядок гарантує контракт репозиторію (ORDER BY id = порядок створення), а не фізичне розміщення рядків.
    actual = [e.reference for e in sut.enrollments.find_enrollments(training.id, S.ENROLLED)]
    assert actual == fixture_order


@pytest.mark.smoke
def test_completion(sut):
    saved = sut.service.enroll(request(student(sut), course(sut)))
    completed = sut.service.complete(saved.id)
    assert completed.status == S.COMPLETED
    assert sut.enrollments.find_by_id(saved.id) == completed


def test_duplicate_enrollment(sut):
    person, training = student(sut), course(sut)
    saved = sut.service.enroll(request(person, training))
    with pytest.raises(EnrollmentRejectedError):
        sut.service.enroll(request(person, training))
    assert sut.enrollments.find_by_id(saved.id) == saved
    assert sut.enrollments.count_occupied(training.id) == 1


@pytest.mark.smoke
def test_policy_rejection(sut):
    class DeniedPolicy:
        def is_allowed(self, person, training):
            return False

    training = course(sut)
    service = EnrollmentService(sut.students, sut.courses, sut.enrollments, DeniedPolicy())
    with pytest.raises(EnrollmentRejectedError):
        service.enroll(request(student(sut), training))
    assert sut.enrollments.find_enrollments(training.id, S.ENROLLED) == []


def test_repeated_cancellation(sut):
    saved = sut.service.enroll(request(student(sut), course(sut)))
    cancelled = sut.service.cancel(saved.id, 7)
    with pytest.raises(EnrollmentRejectedError):
        sut.service.cancel(saved.id, 7)
    assert sut.enrollments.find_by_id(saved.id) == cancelled
