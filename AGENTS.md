# Project
Khanza Migrator

## Architecture
Use layered architecture:

app/
  domains/
  application/
  infrastructure/
  presentation/

Domain layer must not depend on PySide6.

## Coding Rules
- Python 3.10+; minimum version and dependencies are defined in `pyproject.toml`.
- Use type hints
- Prefer dataclasses for DTO/value objects
- Keep UI logic out of domain/application layer
- Avoid god classes
- Small focused methods
- Existing behavior must not change during refactoring

## PySide6
- MainWindow only coordinates UI
- Complex dialogs go into separate classes/files
- Long-running operations must not block UI thread
- Workers belong in presentation/qt/workers

## Changes
Before editing:
1. inspect relevant files
2. understand existing flow
3. explain planned changes briefly
4. implement the smallest necessary change

After editing:
1. check imports
2. report files changed
3. explain architectural impact
