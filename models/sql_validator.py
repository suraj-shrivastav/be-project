"""SQL validation for direct LLM-generated SQL queries."""

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from .prompts import columns


class SQLError(Exception):
    """Error during SQL validation."""

    def __init__(self, message: str, sql: Optional[str] = None):
        self.message = message
        self.sql = sql
        super().__init__(message)


@dataclass
class SQLQuery:
    """Validated SQL query with parameters."""

    sql_template: str
    parameters: Dict[str, Any]
    error: Optional[str] = None


class SQLValidator:
    """Validates LLM-generated SQL queries for safety and correctness."""

    ALLOWED_COLUMNS: Set[str] = set(columns)

    FORBIDDEN_KEYWORDS = {
        "DROP", "DELETE", "INSERT", "UPDATE", "CREATE", "ALTER",
        "GRANT", "REVOKE", "UNION", "EXEC", "EXECUTE", "MERGE",
        "TRUNCATE", "REPLACE", "CALL", "SHOW", "DESCRIBE",
        # Postgres-specific danger
        "COPY", "VACUUM", "PG_", "INFORMATION_SCHEMA",
    }

    REQUIRED_TABLES = ["fundamentals"]

    PARAMETER_PATTERN = r"\$\d+"
    COLUMN_PATTERN = r'"([^"]+)"'

    # Allow short string literals in WHERE clauses (e.g., country = 'US')
    # but cap how many quoted strings we tolerate to keep injection surface tight.
    MAX_STRING_LITERALS = 6
    MAX_LITERAL_LEN = 64

    def validate_sql_output(self, json_output: str) -> SQLQuery:
        """Validate LLM JSON output and return SQLQuery object."""
        try:
            data = json.loads(json_output)
        except json.JSONDecodeError as e:
            return SQLQuery(sql_template="", parameters={}, error=f"Invalid JSON: {e}")

        for field in ("sql_template", "parameters", "error"):
            if field not in data:
                return SQLQuery(sql_template="", parameters={}, error=f"Missing field: {field}")

        if data.get("error"):
            return SQLQuery(
                sql_template=data.get("sql_template", ""),
                parameters=data.get("parameters", {}),
                error=data["error"],
            )

        sql = self._enforce_limit(data["sql_template"])

        try:
            self._validate_sql_template(sql)
            self._validate_parameters(sql, data["parameters"])
        except SQLError as e:
            return SQLQuery(sql_template=sql, parameters=data.get("parameters", {}), error=e.message)

        return SQLQuery(sql_template=sql, parameters=data["parameters"], error=None)

    def _enforce_limit(self, sql: str) -> str:
        """Ensure every query is bounded — append LIMIT 20 if missing.

        Beginners want a short list, not 500 rows. This is the safety net
        when the LLM forgets the LIMIT it was instructed to include.
        """
        upper = sql.upper()
        if "LIMIT" not in upper:
            return sql.rstrip().rstrip(";") + " LIMIT 20"
        return sql

    def _validate_sql_template(self, sql: str) -> None:
        if not sql:
            raise SQLError("Empty SQL template")

        sql_upper = sql.upper()

        for keyword in self.FORBIDDEN_KEYWORDS:
            # Use word-boundary check so 'CREATED_AT' doesn't trigger 'CREATE'
            if re.search(rf"\b{keyword}\b", sql_upper):
                raise SQLError(f"Forbidden keyword: {keyword}")

        if not sql_upper.strip().startswith("SELECT"):
            raise SQLError("Only SELECT statements are allowed")

        if not any(re.search(rf"\b{t}\b", sql, re.IGNORECASE) for t in self.REQUIRED_TABLES):
            raise SQLError("Must query the fundamentals table")

        # Column whitelist
        for col in re.findall(self.COLUMN_PATTERN, sql):
            if col not in self.ALLOWED_COLUMNS:
                raise SQLError(f"Unsupported column: {col}")

        # String literals — allow a small bounded number of short ones
        literals = re.findall(r"'([^']*)'", sql)
        if len(literals) > self.MAX_STRING_LITERALS:
            raise SQLError("Too many string literals")
        for lit in literals:
            if len(lit) > self.MAX_LITERAL_LEN:
                raise SQLError("String literal too long")

        if "--" in sql or "/*" in sql:
            raise SQLError("SQL comments not allowed")

        if ";" in sql.rstrip().rstrip(";"):
            raise SQLError("Multiple statements not allowed")

    def _validate_parameters(self, sql: str, parameters: Dict[str, Any]) -> None:
        sql_params = set(re.findall(self.PARAMETER_PATTERN, sql))
        param_keys = set(parameters.keys())

        missing = sql_params - param_keys
        if missing:
            raise SQLError(f"Missing parameters: {missing}")

        extra = param_keys - sql_params
        if extra:
            raise SQLError(f"Extra parameters: {extra}")

        for key, value in parameters.items():
            if not isinstance(value, (int, float)):
                raise SQLError(f"Parameter {key} must be numeric, got {type(value).__name__}")
            if isinstance(value, float) and (value != value or abs(value) == float("inf")):
                raise SQLError(f"Parameter {key} has invalid value: {value}")

    def get_used_columns(self, sql: str) -> List[str]:
        return list(set(re.findall(self.COLUMN_PATTERN, sql)))

    def is_safe_sql(self, sql: str) -> bool:
        try:
            self._validate_sql_template(sql)
            return True
        except SQLError:
            return False
