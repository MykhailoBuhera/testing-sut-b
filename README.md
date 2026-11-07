# testing-sut-b

[![CI](https://github.com/MykhailoBuhera/testing-sut-b/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/MykhailoBuhera/testing-sut-b/actions/workflows/ci.yml)

ЛР9 «CI/CD and Continuous Testing». SUT B, варіант 03 — «Реєстрація на навчальні курси» (Python 3.12, FastAPI, PostgreSQL).

| Папка | Вміст |
|---|---|
| `sut-b/` | Backend, тести та нотатки ЛР8/ЛР9 — див. [`sut-b/LAB9_NOTES.md`](sut-b/LAB9_NOTES.md) |
| `sut-c-ui/` | Готовий UI, який віддає backend |
| `.github/workflows/ci.yml` | CI pipeline |

Pipeline: **Build → Unit tests → Integration tests (Testcontainers) ∥ API tests (PostgreSQL service)**.

Pipeline запускається на кожен push і на кожен pull request у `main`. Звіти тестів (JUnit XML + HTML) зберігаються як artifacts кожного запуску.
