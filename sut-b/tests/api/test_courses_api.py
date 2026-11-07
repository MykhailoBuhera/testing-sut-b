"""API / integration tests: HTTP-рівень FastAPI → EnrollmentService → PostgreSQL.

Потрібна PostgreSQL із застосованою міграцією V1 (змінні DB_* як у .env.example).
⚠️ Перед кожним тестом таблиці очищаються — запускати лише на тестовій БД.
"""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from courses.api import app
from courses.database import connect


@pytest.fixture(autouse=True)
def clean_database():
    with connect() as db:
        db.execute("TRUNCATE enrollments, students, courses RESTART IDENTITY CASCADE")
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def code(prefix):
    return f"{prefix}-{uuid4().hex[:8]}"


def create_student(client, regular=False, age=20, entry_score=90):
    response = client.post("/students", json={"code": code("ST"), "age": age, "entry_score": entry_score, "regular": regular})
    assert response.status_code == 201, response.text
    return response.json()


def create_course(client, price=100000, capacity=10, level=1):
    response = client.post("/courses", json={"code": code("CR"), "price": price, "capacity": capacity, "level": level})
    assert response.status_code == 201, response.text
    return response.json()


def enroll(client, student, course):
    return client.post("/enrollments", json={"reference": code("EN"), "student_id": student["id"],
                                             "course_id": course["id"], "coupon_valid": False})


def test_create_and_get_student(client):
    created = create_student(client)
    response = client.get(f"/students/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


def test_location_header_points_to_resource(client):
    response = client.post("/courses", json={"code": code("CR"), "price": 100000, "capacity": 5, "level": 2})
    assert response.status_code == 201
    assert client.get(response.headers["Location"]).json() == response.json()


def test_enrollment_with_regular_discount_persisted(client):
    student, course = create_student(client, regular=True), create_course(client, price=100000)
    response = enroll(client, student, course)
    assert response.status_code == 201
    saved = client.get(response.headers["Location"]).json()
    assert (saved["status"], saved["total"], saved["prepaid"], saved["refund"]) == ("Enrolled", 90000, 90000, 0)


def test_cancel_returns_full_prepaid(client):
    enrollment = enroll(client, create_student(client), create_course(client, price=150000)).json()
    response = client.post(f"/enrollments/{enrollment['id']}/cancel", json={"days_before_start": 7})
    assert response.status_code == 200
    stored = client.get(f"/enrollments/{enrollment['id']}").json()
    assert (stored["status"], stored["refund"], stored["prepaid"]) == ("Cancelled", 150000, 150000)


def test_complete_then_cancel_is_conflict(client):
    enrollment = enroll(client, create_student(client), create_course(client)).json()
    assert client.post(f"/enrollments/{enrollment['id']}/complete").json()["status"] == "Completed"
    response = client.post(f"/enrollments/{enrollment['id']}/cancel", json={"days_before_start": 0})
    assert response.status_code == 409
    assert response.json()["code"] == "ENROLLMENT_CONFLICT"
    assert client.get(f"/enrollments/{enrollment['id']}").json()["status"] == "Completed"


def test_full_course_is_conflict_and_not_persisted(client):
    course = create_course(client, capacity=1)
    assert enroll(client, create_student(client), course).status_code == 201
    response = enroll(client, create_student(client), course)
    assert response.status_code == 409
    with connect() as db:
        assert db.execute("SELECT count(*) FROM enrollments WHERE course_id=%s", (course["id"],)).fetchone()[0] == 1


@pytest.mark.parametrize("age", [13, 101])
def test_invalid_student_age_is_bad_request(client, age):
    response = client.post("/students", json={"code": code("ST"), "age": age, "entry_score": 50, "regular": False})
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"


def test_duplicate_student_code_is_conflict(client):
    body = {"code": code("ST"), "age": 20, "entry_score": 50, "regular": False}
    assert client.post("/students", json=body).status_code == 201
    response = client.post("/students", json=body)
    assert response.status_code == 409
    assert response.json()["code"] == "DUPLICATE_RESOURCE"


@pytest.mark.parametrize("path", ["/students/999", "/courses/999", "/enrollments/999"])
def test_missing_resource_is_not_found(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
