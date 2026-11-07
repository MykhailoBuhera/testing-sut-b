# ЛР9 — CI/CD and Continuous Testing

SUT B, варіант 03 — «Реєстрація на навчальні курси». Платформа: **GitHub Actions**.

## Що додано

| Файл | Призначення |
|---|---|
| `../.github/workflows/ci.yml` | CI pipeline (лежить у корені репозиторію) |
| `tests/unit/test_enrollment_service_unit.py` | Unit tests сервісу: in-memory двійники за `courses/ports.py`, без БД (51 тест) |
| `tests/api/test_courses_api.py` | API tests: FastAPI `TestClient` → сервіс → реальна PostgreSQL (12 тестів) |
| `requirements-ci.txt` | Додаткові пакети для CI: `httpx` (TestClient), `pytest-html` (HTML-звіт) |
| `../.gitignore` | Звіти, `dist/`, `.venv` не потрапляють у репозиторій |

Integration stage перевикористовує наявний regression suite з ЛР8 (`tests/enrollment_regression.py`, PostgreSQL через Testcontainers).

## Структура pipeline

```
push / pull_request ──► Build ──► Unit tests ──┬──► Integration tests (regression, Testcontainers)
                                               └──► API tests (FastAPI + PostgreSQL service)
```

| Job | Що робить | Report / artifact |
|---|---|---|
| **Build** | `python -m build` → wheel + sdist; встановлення wheel і імпорт `courses.api` | `dist` |
| **Unit tests** | `pytest tests/unit` | `unit-test-report` (JUnit XML + HTML), check «Unit tests report» |
| **Integration tests** | `pytest tests/enrollment_regression.py -m regression -p randomly` | `integration-test-report`, check «Integration tests report» |
| **API tests** | service container `postgres:17` → міграція через `psql` → `pytest tests/api tests/test_environment.py` | `api-test-report`, check «API tests report» |

Невдалий етап не приховується: немає `continue-on-error` чи `|| true`. Якщо pytest повертає ненульовий код, job і весь workflow отримують статус **failed**, а залежні jobs — **skipped**. `if: always()` стоїть лише на кроках завантаження звітів, щоб звіт був доступний саме у випадку падіння.

## Тригери

- `push` у будь-яку гілку;
- `pull_request` у `main`;
- `workflow_dispatch` — ручний запуск як доповнення, не замість автоматичного.

`concurrency` скасовує застарілий запуск тієї самої гілки, коли приходить новий push.

## Локальний запуск (із папки `sut-b`)

```powershell
python -m pip install -e ".[regression]" -r requirements-ci.txt

python -m pytest tests/unit -v                    # без БД
python -m pytest tests/api tests/test_environment.py -v   # потрібна локальна PostgreSQL + DB_* змінні
python -m pytest tests/enrollment_regression.py -m regression -v   # потрібен Docker
```

⚠️ API tests виконують `TRUNCATE ... RESTART IDENTITY` перед кожним тестом. Запускай їх лише на тестовій БД.

## Репозиторій

https://github.com/MykhailoBuhera/testing-sut-b — вкладка **Actions** → workflow **CI**.

## Демо: failed pipeline

Навмисна помилка на межі персонального правила (BVA з ЛР1): знижка 10% має діяти від 1000 грн **включно**.

```powershell
git checkout -b demo/failing-pipeline
# sut-b/courses/service.py, calculate_total:  price >= 100000  →  price > 100000
git commit -am "demo: boundary bug in regular discount"
git push -u origin demo/failing-pipeline
```

Далі відкрий Pull Request `demo/failing-pipeline → main`.

Очікуваний результат:

- **Unit tests** ✗;
- Integration / API — skipped;
- PR має червоний check.

Причину видно в трьох місцях:

1. **Лог кроку «Run unit tests»** — `FAILED test_calculate_total[100000-True-90000]` та `test_cancel_records_refund_and_keeps_amounts`, `assert 100000 == 90000`.
2. **Check «Unit tests report» / Summary** — таблиця з назвою тесту, файлом і повідомленням assert.
3. **Artifact `unit-test-report`** — `unit-report.html` з деталями та `unit-junit.xml`.

Повернення до зеленого стану: виправити `>`→`>=` і зробити push, або закрити PR без merge.

## Терміни

- **CI (Continuous Integration)** — автоматичне збирання й тестування після кожної зміни в репозиторії. Тут це кожен push і PR.
- **Pipeline** — workflow `CI` з `ci.yml`: послідовність jobs build → unit → integration/API.
- **Stage / Job** — окремий крок pipeline (`build`, `unit-tests`, `integration-tests`, `api-tests`). Порядок задає `needs`. Integration і API виконуються паралельно, бо обидва залежать лише від unit.
- **Trigger** — подія з блоку `on:` (`push`, `pull_request`, `workflow_dispatch`).
- **Runner** — середовище, де виконується job: `ubuntu-latest`, VM від GitHub. Кожен job отримує чисту VM.
- **Artifact / Test report** — збережений результат: wheel у `dist`, JUnit XML + HTML-звіти тестів. Їх можна завантажити зі сторінки запуску.
