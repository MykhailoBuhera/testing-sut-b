"""Unit tests для EnrollmentService: без БД, залежності — in-memory тестові двійники за контрактами courses/ports.py."""
from dataclasses import replace

import pytest

from courses.models import Course, EnrollmentRequest, EnrollmentStatus as S, Student
from courses.service import EnrollmentRejectedError, EnrollmentService, ResourceNotFoundError


class InMemoryStudents:
    def __init__(self):
        self.items, self.next_id = {}, 1

    def create(self, student):
        saved = replace(student, id=self.next_id)
        self.items[saved.id] = saved
        self.next_id += 1
        return saved

    def find_by_id(self, student_id):
        return self.items.get(student_id)

    def update(self, student):
        if student.id not in self.items:
            raise ValueError("Слухача не знайдено")
        self.items[student.id] = student

    def delete(self, student_id):
        self.items.pop(student_id, None)


class InMemoryCourses(InMemoryStudents):
    pass


class InMemoryEnrollments:
    def __init__(self):
        self.items, self.next_id = {}, 1

    def create(self, enrollment):
        saved = replace(enrollment, id=self.next_id)
        self.items[saved.id] = saved
        self.next_id += 1
        return saved

    def find_by_id(self, enrollment_id):
        return self.items.get(enrollment_id)

    def find_enrollments(self, course_id, status):
        return sorted((e for e in self.items.values() if e.course_id == course_id and e.status == status),
                      key=lambda e: e.id)

    def count_occupied(self, course_id):
        return sum(1 for e in self.items.values() if e.course_id == course_id and e.status != S.CANCELLED)

    def has_enrollment(self, student_id, course_id):
        return any(e.student_id == student_id and e.course_id == course_id and e.status != S.CANCELLED
                   for e in self.items.values())

    def update_status(self, enrollment_id, next_status, refund):
        current = self.items.get(enrollment_id)
        if current is None or current.status != S.ENROLLED:
            raise EnrollmentRejectedError("Активну заявку не знайдено")
        self.items[enrollment_id] = replace(current, status=next_status, refund=refund)

    def delete(self, enrollment_id):
        self.items.pop(enrollment_id, None)


class SpyPolicy:
    def __init__(self, answer=True, error=None):
        self.answer, self.error, self.calls = answer, error, []

    def is_allowed(self, student, course):
        self.calls.append((student.id, course.id))
        if self.error:
            raise self.error
        return self.answer


@pytest.fixture
def env():
    students, courses, enrollments, policy = InMemoryStudents(), InMemoryCourses(), InMemoryEnrollments(), SpyPolicy()
    service = EnrollmentService(students, courses, enrollments, policy)
    return service, students, courses, enrollments, policy


def add_student(students, regular=False):
    return students.create(Student(0, f"ST-{students.next_id}", 20, 90, regular))


def add_course(courses, price=100000, capacity=10):
    return courses.create(Course(0, f"CR-{courses.next_id}", price, capacity, 1))


def req(reference, student, course, coupon_valid=False):
    return EnrollmentRequest(reference, student.id, course.id, coupon_valid)


# ---------- calculate_total: персональне правило (10% постійному від 1000 грн включно) ----------

@pytest.mark.parametrize("price, regular, expected", [
    (99999, True, 99999),       # нижче порогу — без знижки
    (100000, True, 90000),      # поріг включно
    (100001, True, 90001),      # 10% округлюється донизу один раз
    (100000, False, 100000),    # не постійний — без знижки
    (10000000, True, 9000000),  # верхня межа ціни
    (100, True, 100),           # нижня межа ціни
])
def test_calculate_total(env, price, regular, expected):
    service = env[0]
    assert service.calculate_total(price, regular) == expected


def test_coupon_does_not_affect_total(env):
    service = env[0]
    assert service.calculate_total(100000, False, coupon_valid=True) == 100000


@pytest.mark.parametrize("price", [99, 10000001, True, 1000.0])
def test_calculate_total_rejects_invalid_price(env, price):
    with pytest.raises(ValueError):
        env[0].calculate_total(price)


@pytest.mark.parametrize("flag", [1, "true", None])
def test_calculate_total_rejects_non_bool_flags(env, flag):
    with pytest.raises(ValueError):
        env[0].calculate_total(100000, regular=flag)


# ---------- is_eligible / calculate_refund ----------

@pytest.mark.parametrize("age, score, level", [(14, 0, 1), (100, 100, 3)])
def test_is_eligible_on_bounds(env, age, score, level):
    assert env[0].is_eligible(age, score, level) is True


@pytest.mark.parametrize("age, score, level", [(13, 50, 1), (101, 50, 1), (20, -1, 1), (20, 101, 1), (20, 50, 0), (20, 50, 4)])
def test_is_eligible_rejects_out_of_range(env, age, score, level):
    with pytest.raises(ValueError):
        env[0].is_eligible(age, score, level)


@pytest.mark.parametrize("days", [0, 7, 36500])
def test_refund_is_full_prepaid(env, days):
    assert env[0].calculate_refund(90000, days) == 90000


@pytest.mark.parametrize("prepaid, days", [(-1, 0), (10000001, 0), (100, -1), (100, 36501)])
def test_refund_rejects_out_of_range(env, prepaid, days):
    with pytest.raises(ValueError):
        env[0].calculate_refund(prepaid, days)


# ---------- enroll ----------

def test_enroll_success_with_discount(env):
    service, students, courses, enrollments, policy = env
    person, training = add_student(students, regular=True), add_course(courses, price=200000)
    saved = service.enroll(req("EN-1", person, training))
    assert (saved.status, saved.total, saved.prepaid, saved.refund) == (S.ENROLLED, 180000, 180000, 0)
    assert enrollments.find_by_id(saved.id) == saved
    assert policy.calls == [(person.id, training.id)]


def test_duplicate_enrollment_rejected_without_policy_call(env):
    service, students, courses, enrollments, policy = env
    person, training = add_student(students), add_course(courses)
    service.enroll(req("EN-1", person, training))
    with pytest.raises(EnrollmentRejectedError):
        service.enroll(req("EN-2", person, training))
    assert len(policy.calls) == 1
    assert enrollments.count_occupied(training.id) == 1


def test_full_course_rejected_without_policy_call(env):
    service, students, courses, enrollments, policy = env
    training = add_course(courses, capacity=1)
    service.enroll(req("EN-1", add_student(students), training))
    with pytest.raises(EnrollmentRejectedError):
        service.enroll(req("EN-2", add_student(students), training))
    assert len(policy.calls) == 1


def test_cancelled_enrollment_frees_seat(env):
    service, students, courses, _, _ = env
    training = add_course(courses, capacity=1)
    first = service.enroll(req("EN-1", add_student(students), training))
    service.cancel(first.id)
    assert service.enroll(req("EN-2", add_student(students), training)).status == S.ENROLLED


def test_completed_enrollment_keeps_seat(env):
    service, students, courses, _, _ = env
    training = add_course(courses, capacity=1)
    first = service.enroll(req("EN-1", add_student(students), training))
    service.complete(first.id)
    with pytest.raises(EnrollmentRejectedError):
        service.enroll(req("EN-2", add_student(students), training))


def test_policy_false_rejects_and_saves_nothing(env):
    service, students, courses, enrollments, policy = env
    policy.answer = False
    training = add_course(courses)
    with pytest.raises(EnrollmentRejectedError):
        service.enroll(req("EN-1", add_student(students), training))
    assert enrollments.items == {}


def test_policy_non_bool_answer_is_value_error(env):
    service, students, courses, enrollments, policy = env
    policy.answer = "yes"
    with pytest.raises(ValueError):
        service.enroll(req("EN-1", add_student(students), add_course(courses)))
    assert enrollments.items == {}


def test_policy_exception_propagates(env):
    service, students, courses, enrollments, policy = env
    policy.error = TimeoutError("policy down")
    with pytest.raises(TimeoutError):
        service.enroll(req("EN-1", add_student(students), add_course(courses)))
    assert enrollments.items == {}


def test_missing_student_and_course(env):
    service, students, courses, _, policy = env
    person, training = add_student(students), add_course(courses)
    with pytest.raises(ResourceNotFoundError):
        service.enroll(EnrollmentRequest("EN-1", 999, training.id))
    with pytest.raises(ResourceNotFoundError):
        service.enroll(EnrollmentRequest("EN-1", person.id, 999))
    assert policy.calls == []


@pytest.mark.parametrize("reference", ["", "   ", "x" * 41, 123])
def test_invalid_reference(env, reference):
    service, students, courses, _, _ = env
    with pytest.raises(ValueError):
        service.enroll(EnrollmentRequest(reference, add_student(students).id, add_course(courses).id))


def test_reference_max_length_accepted(env):
    service, students, courses, _, _ = env
    assert service.enroll(req("x" * 40, add_student(students), add_course(courses))).reference == "x" * 40


# ---------- complete / cancel ----------

def test_cancel_records_refund_and_keeps_amounts(env):
    service, students, courses, enrollments, _ = env
    saved = service.enroll(req("EN-1", add_student(students, regular=True), add_course(courses, price=100000)))
    cancelled = service.cancel(saved.id, 7)
    assert (cancelled.status, cancelled.total, cancelled.prepaid, cancelled.refund) == (S.CANCELLED, 90000, 90000, 90000)
    assert enrollments.find_by_id(saved.id) == cancelled


def test_complete_keeps_amounts(env):
    service, students, courses, _, _ = env
    saved = service.enroll(req("EN-1", add_student(students), add_course(courses)))
    completed = service.complete(saved.id)
    assert completed == replace(saved, status=S.COMPLETED)


@pytest.mark.parametrize("first, second", [("complete", "complete"), ("complete", "cancel"),
                                           ("cancel", "cancel"), ("cancel", "complete")])
def test_forbidden_transitions(env, first, second):
    service, students, courses, _, _ = env
    saved = service.enroll(req("EN-1", add_student(students), add_course(courses)))
    getattr(service, first)(saved.id)
    with pytest.raises(EnrollmentRejectedError):
        getattr(service, second)(saved.id)


def test_transitions_do_not_call_policy(env):
    service, students, courses, _, policy = env
    saved = service.enroll(req("EN-1", add_student(students), add_course(courses)))
    service.cancel(saved.id)
    assert len(policy.calls) == 1


def test_unknown_enrollment_not_found(env):
    with pytest.raises(ResourceNotFoundError):
        env[0].complete(999)
