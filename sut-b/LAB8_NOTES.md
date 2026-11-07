# ЛР8 — Regression Testing and Flaky Test Debugging

SUT B, варіант 03 — «Реєстрація на навчальні курси» (Python + PostgreSQL через Testcontainers).

## Файли

| Файл | Призначення |
|---|---|
| `tests/enrollment_regression.py` | Виправлений regression suite (smoke + regression маркери) |
| `tests/enrollment_regression_original.py` | Оригінальний набір викладача з навмисними проблемами — для демонстрації виявлення |
| `pyproject.toml` | Маркери `smoke` / `regression`, `--strict-markers`, плагіни `pytest-randomly`, `pytest-repeat` |

Обидва файли тестів не мають префікса `test_`, тому голий `python -m pytest` їх не збирає — шлях передається явно.

## Підготовка

Потрібен запущений Docker Desktop. Із папки `sut-b`:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[regression]"
```

## Smoke та regression

- Усі тести модуля мають `pytestmark = pytest.mark.regression`.
- Критичні сценарії додатково мають `@pytest.mark.smoke`: `test_create_enrollment`, `test_cancel_enrollment`, `test_completion`, `test_policy_rejection`.
- Отже smoke — підмножина regression, а повний regression run включає smoke.

```powershell
# smoke subset — 4 тести
python -m pytest tests/enrollment_regression.py -m smoke -v

# повний regression suite — 10 тестів, включно зі smoke
python -m pytest tests/enrollment_regression.py -m regression -v
```

## Виявлення flaky tests (на оригінальному наборі)

```powershell
# 1. Звичайний прогін у фіксованому порядку — падають policy_check і student_score
python -m pytest tests/enrollment_regression_original.py -p no:randomly -v

# 2. Ізольований запуск — cancel падає без create
python -m pytest "tests/enrollment_regression_original.py::test_cancel_enrollment" -p no:randomly
python -m pytest "tests/enrollment_regression_original.py::test_student_score" -p no:randomly

# 3. Змінений порядок — результат залежить від seed
python -m pytest tests/enrollment_regression_original.py -p randomly --randomly-seed=1
python -m pytest tests/enrollment_regression_original.py -p randomly --randomly-seed=2

# 4. Змінене оточення
$env:REGRESSION_POLICY_DELAY_MS="0"
python -m pytest tests/enrollment_regression_original.py -k policy_check -p no:randomly
Remove-Item Env:REGRESSION_POLICY_DELAY_MS

$env:REGRESSION_REVERSE_FIXTURE="true"
python -m pytest tests/enrollment_regression_original.py -k selection -p no:randomly
Remove-Item Env:REGRESSION_REVERSE_FIXTURE
```

## Проблеми: симптом → причина → виправлення

### 1. Order dependency — `test_cancel_enrollment`
- **Симптом:** у повному прогоні в стандартному порядку проходить; запущений окремо або раніше за `test_create_enrollment` — `ValueError: Ціле число поза допустимими межами`.
- **Причина:** тест використовує глобальну змінну `enrollment_id`, яку заповнює інший тест. Без нього `enrollment_id = 0`.
- **Виправлення:** тест сам створює власну заявку; глобальну змінну видалено.

### 2. Shared state / dirty test data — `test_student_age`, `test_student_score`
- **Симптом:** кожен окремо проходить; разом другий падає з `UniqueViolation` (`students_code_key`).
- **Причина:** обидва створюють слухача з однаковим `code = "REGRESSION-STUDENT"` у спільній БД (контейнер module-scoped), а `code` унікальний.
- **Виправлення:** унікальний код через `uuid4()`; autouse-фікстура `clean_database` виконує `TRUNCATE enrollments, students, courses CASCADE` перед кожним тестом.

### 3. Fixed wait / timing dependency — `test_enrollment_after_policy_check`
- **Симптом:** зі стандартною затримкою policy (200 мс) падає `assert len(saved) == 1`; з `REGRESSION_POLICY_DELAY_MS=0` здебільшого проходить — результат залежить від швидкості машини.
- **Причина:** тест чекає фіксовані `time.sleep(0.05)` і перевіряє БД, поки `enroll` у потоці ще не завершився.
- **Виправлення:** condition-based wait — `operation.result(timeout=...)`: тест чекає завершення операції, timeout лише верхня межа.

### 4. Недетермінований порядок / залежність від оточення — `test_enrollment_selection`
- **Симптом:** з `REGRESSION_REVERSE_FIXTURE=true` падає: отримано `[B, A]`, очікувано `[A, B]`.
- **Причина:** `SELECT` без `ORDER BY` — порядок рядків SQL не гарантує; очікування захардкоджене і не залежить від порядку створення.
- **Виправлення:** вибірка через `find_enrollments` (контракт: `ORDER BY id`), порівняння з фактичним порядком створення `fixture_order`.

Retry ніде не використано — кожна причина усунена.

## Незалежний запуск окремого test case

```powershell
python -m pytest "tests/enrollment_regression.py::test_cancel_enrollment" -p no:randomly -v
python -m pytest "tests/enrollment_regression.py::test_student_score" -p no:randomly -v
```

## Підтвердження стабільності

```powershell
# кожен тест 5 разів, випадковий порядок
python -m pytest tests/enrollment_regression.py --count=5 -p randomly

# умови, що раніше ламали тести
$env:REGRESSION_POLICY_DELAY_MS="1000"; $env:REGRESSION_REVERSE_FIXTURE="true"
python -m pytest tests/enrollment_regression.py -v
Remove-Item Env:REGRESSION_POLICY_DELAY_MS, Env:REGRESSION_REVERSE_FIXTURE

# три послідовні прогони
1..3 | % { python -m pytest tests/enrollment_regression.py -q }
```

## Теоретичні питання

- **Smoke vs regression:** smoke — швидка перевірка критичних сценаріїв (чи система взагалі працює); regression — повна перевірка, що зміни не зламали наявну функціональність. Smoke ⊂ regression.
- **Failing vs flaky:** failing падає стабільно за тих самих умов (дефект коду або тесту); flaky за незмінного коду то проходить, то падає.
- **Fixed wait vs condition-based wait:** fixed wait чекає вгаданий час — або завчасно (тест падає), або задовго (тест повільний); condition-based чекає конкретну подію з верхньою межею timeout.
- **Retry vs усунення причини:** retry лише зменшує частоту видимих падінь і маскує дефект; виправлення прибирає саму залежність (порядок, спільні дані, таймінг).
