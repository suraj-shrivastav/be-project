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

    # Allowed columns from the dataset
    ALLOWED_COLUMNS: Set[str] = set(columns)

    # Allowed SQL keywords and patterns
    FORBIDDEN_KEYWORDS = {
        "DROP",
        "DELETE",
        "INSERT",
        "UPDATE",
        "CREATE",
        "ALTER",
        "GRANT",
        "REVOKE",
        "UNION",
        "EXEC",
        "EXECUTE",
        "MERGE",
        "TRUNCATE",
        "REPLACE",
        "CALL",
        "SHOW",
        "DESCRIBE",
    }

    # Required table pattern
    REQUIRED_TABLE_PATTERN = ["fundamentals", "prices"]

    # Parameter pattern ($1, $2, etc.)
    PARAMETER_PATTERN = r"\$\d+"

    # Column name pattern (quoted identifiers)
    COLUMN_PATTERN = r'"([^"]+)"'

    def validate_sql_output(self, json_output: str) -> SQLQuery:
        """Validate LLM JSON output and return SQLQuery object."""
        try:
            data = json.loads(json_output)
        except json.JSONDecodeError as e:
            return SQLQuery(sql_template="", parameters={}, error=f"Invalid JSON: {e}")

        # Check required fields
        required_fields = ["sql_template", "parameters", "error"]
        for field in required_fields:
            if field not in data:
                return SQLQuery(
                    sql_template="", parameters={}, error=f"Missing field: {field}"
                )

        # If there's already an error in the output, return it
        if data.get("error"):
            return SQLQuery(
                sql_template=data.get("sql_template", ""),
                parameters=data.get("parameters", {}),
                error=data["error"],
            )

        # Validate the SQL template
        try:
            self._validate_sql_template(data["sql_template"])
        except SQLError as e:
            return SQLQuery(
                sql_template=data.get("sql_template", ""),
                parameters=data.get("parameters", {}),
                error=e.message,
            )

        # Validate parameters
        try:
            self._validate_parameters(data["sql_template"], data["parameters"])
        except SQLError as e:
            return SQLQuery(
                sql_template=data.get("sql_template", ""),
                parameters=data.get("parameters", {}),
                error=e.message,
            )

        return SQLQuery(
            sql_template=data["sql_template"], parameters=data["parameters"], error=None
        )

    def _validate_sql_template(self, sql: str) -> None:
        """Validate the SQL template for safety and correctness."""
        if not sql:
            raise SQLError("Empty SQL template")

        # Convert to uppercase for keyword checking
        sql_upper = sql.upper()

        # Check for forbidden keywords
        for keyword in self.FORBIDDEN_KEYWORDS:
            if keyword in sql_upper:
                raise SQLError(f"Forbidden keyword: {keyword}")

        # Must be a SELECT statement
        if not sql_upper.strip().startswith("SELECT"):
            raise SQLError("Only SELECT statements are allowed")

        # Must contain the required table

        if not any(re.search(table, sql) for table in self.REQUIRED_TABLE_PATTERN):
            raise SQLError("Must use a table in the query")

        # Extract and validate column names
        column_matches = re.findall(self.COLUMN_PATTERN, sql)
        for column_name in column_matches:
            if column_name not in self.ALLOWED_COLUMNS:
                raise SQLError(f"Unsupported column: {column_name}")

        # Extract parameters from SQL
        sql_params = set(re.findall(self.PARAMETER_PATTERN, sql))

        # Check for dangerous patterns
        # Allow single quotes only in read_parquet function
        if sql.count("'") > 2:  # read_parquet has exactly 2 quotes
            raise SQLError("Too many single quotes - potential injection")

        # Check for comments
        if "--" in sql or "/*" in sql:
            raise SQLError("SQL comments not allowed")

        # Check for multiple statements
        if ";" in sql:
            raise SQLError("Multiple statements not allowed")

    def _validate_parameters(self, sql: str, parameters: Dict[str, Any]) -> None:
        """Validate that parameters match the SQL template."""
        # Extract parameters from SQL
        sql_params = set(re.findall(self.PARAMETER_PATTERN, sql))
        param_keys = set(parameters.keys())

        # Check for missing parameters
        missing_params = sql_params - param_keys
        if missing_params:
            raise SQLError(f"Missing parameters: {missing_params}")

        # Check for extra parameters
        extra_params = param_keys - sql_params
        if extra_params:
            raise SQLError(f"Extra parameters: {extra_params}")

        # Validate parameter values are numeric
        for param_key, param_value in parameters.items():
            if not isinstance(param_value, (int, float)):
                raise SQLError(
                    f"Parameter {param_key} must be numeric, got {type(param_value)}"
                )

            # Check for NaN or infinity
            if isinstance(param_value, float) and (
                param_value != param_value or abs(param_value) == float("inf")
            ):
                raise SQLError(
                    f"Parameter {param_key} has invalid value: {param_value}"
                )

    def get_used_columns(self, sql: str) -> List[str]:
        """Extract column names used in the SQL."""
        column_matches = re.findall(self.COLUMN_PATTERN, sql)
        return list(set(column_matches))

    def is_safe_sql(self, sql: str) -> bool:
        """Quick safety check without full validation."""
        try:
            self._validate_sql_template(sql)
            return True
        except SQLError:
            return False
