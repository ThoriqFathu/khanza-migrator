from collections.abc import Iterable


def render_statements(statements: Iterable[str]) -> str:
    """Preserve statement boundaries, including routine bodies and trailing comments."""
    sql = list(statements)
    if not sql:
        return ""
    delimiter = "$$KHANZA$$"
    while any(delimiter in statement for statement in sql):
        delimiter += "_"
    return f"DELIMITER {delimiter}\n" + "".join(
        f"{statement}\n{delimiter}\n" for statement in sql
    ) + "DELIMITER ;\n"
