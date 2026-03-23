# Financial Filter Backend

A backend system for stock charting websites that converts natural language queries into optimized DuckDB SQL queries using LLM-generated expressions.

## Architecture

- **Entry Point**: `main.py` - Interactive CLI orchestrating the pipeline
  - **Safety Guard**: `models/guard.py` - Local Qwen Guard model for prompt validation
  - **Filter Model**: `models/filter.py` - OpenRouter API (Qwen3-14B) for NL→expression translation
    - **Prompts**: `models/prompts.py` - System prompt and supported metrics/operators
  - **Compiler**: `compiler/` - Expression parsing and SQL generation
    - **Lexer**: Tokenization of Pythonic expressions
    - **Parser**: Custom AST construction
    - **Codegen**: SQL generation with parameterization
- **Data Layer**: Parquet files in `data/` directory
  - **Fundamentals**: `fundamentals.parquet` - Static company metrics
  - **Time Series**: `data/<TICKER>/<DATE>.parquet` - Minute-by-minute OHLCV data
- **Utilities**: `scripts/` - Data generation and evaluation tools

## Data Structure

### Fundamentals (`data/fundamentals.parquet`)

| Column | Type | Description |
|--------|------|-------------|
| Ticker | STRING | Stock ticker symbol |
| Sector | STRING | Business sector |
| Industry | STRING | Industry classification |
| MarketCap | DOUBLE | Market capitalization |
| PeRatio | DOUBLE | Price-to-earnings ratio |
| PbRatio | DOUBLE | Price-to-book ratio |
| DividendYield | DOUBLE | Dividend yield (decimal) |
| Beta | DOUBLE | Beta coefficient |
| FloatShares | DOUBLE | Number of float shares |

### Time Series (`data/<TICKER>/<YYYY-MM-DD>.parquet`)

| Column | Type | Description |
|--------|------|-------------|
| Datetime | TIMESTAMP | Minute timestamp |
| Open | DOUBLE | Opening price |
| High | DOUBLE | High price |
| Low | DOUBLE | Low price |
| Close | DOUBLE | Closing price |
| PreviousClose | DOUBLE | Previous day's close |
| ShareVolume | DOUBLE | Trading volume |
| Value | DOUBLE | Total value traded |
| 30DayPercentageChange | DOUBLE | 30-day return (decimal) |
| 365DayPercentageChange | DOUBLE | 365-day return (decimal) |
| EarningsPerShare | DOUBLE | Earnings per share |

## Expression Grammar

The system uses a Pythonic function-call syntax that LLMs generate reliably.

### Operators

#### Comparison Operators
- `gt(left, right)` - Greater than (>)  
- `lt(left, right)` - Less than (<)
- `geq(left, right)` - Greater than or equal (>=)
- `leq(left, right)` - Less than or equal (<=)
- `neq(left, right)` - Not equal (!=)

#### Logical Operators
- `and(expr1, expr2, ...)` - Logical AND (all must be true)
- `or(expr1, expr2, ...)` - Logical OR (any must be true)
- `not(expr)` - Logical NOT

#### Arithmetic Operators
- `add(left, right)` - Addition (+)
- `sub(left, right)` - Subtraction (-)
- `mul(left, right)` - Multiplication (*)
- `div(left, right)` - Division (/)

#### Query Modifiers
- `order_by(expr, column, direction="asc")` - Sort results
  - `direction`: `"asc"` or `"desc"`
- `limit(expr, count, offset=0)` - Limit results with optional offset

### Operands

#### Metrics (Columns)
Literal column names in CamelCase:
- `MarketCap`, `PeRatio`, `PbRatio`, `DividendYield`, `Beta`, `FloatShares`
- `Open`, `High`, `Low`, `Close`, `PreviousClose`
- `ShareVolume`, `Value`, `30DayPercentageChange`, `365DayPercentageChange`
- `EarningsPerShare`, `Ticker`, `Sector`, `Industry`, `Datetime`

#### Numbers
- Integers: `100`, `1000000`
- Decimals: `0.05`, `1.5`
- Scientific notation: `1e9`, `2.5e10` (converted to actual numbers)

### Grammar Rules

```
<expr> ::= <filter_expr>
         | <modifier_expr>

<filter_expr> ::= <logic_op>
                | <comp_op>
                | <arith_op>
                | <metric>
                | <number>

<modifier_expr> ::= order_by(<filter_expr>, <metric>)
                  | order_by(<filter_expr>, <metric>, <direction>)
                  | limit(<filter_expr>, <count>)
                  | limit(<filter_expr>, <count>, offset=<offset>)

<logic_op> ::= and(<filter_expr>, <filter_expr>, ...)
             | or(<filter_expr>, <filter_expr>, ...)
             | not(<filter_expr>)

<comp_op> ::= gt(<expr>, <expr>)
            | lt(<expr>, <expr>)
            | geq(<expr>, <expr>)
            | leq(<expr>, <expr>)
            | neq(<expr>, <expr>)

<arith_op> ::= add(<expr>, <expr>)
             | sub(<expr>, <expr>)
             | mul(<expr>, <expr>)
             | div(<expr>, <expr>)

<metric> ::= Identifier (CamelCase column name)
<number> ::= Integer | Decimal | Scientific
<direction> ::= "asc" | "desc"
<count> ::= Positive integer
<offset> ::= Non-negative integer
```

### Examples

```python
# Simple comparison
gt(MarketCap, 1e9)

# Logical combination
and(gt(MarketCap, 1e9), lt(PeRatio, 20))

# Arithmetic in comparison
gt(sub(High, Low), 5)

# Complex nesting
or(
    and(gt(MarketCap, 2e11), lt(PeRatio, 20), gt(DividendYield, 0.01)),
    lt(Beta, 0.5)
)

# With ordering and limiting
limit(order_by(gt(MarketCap, 1e9), MarketCap, "desc"), 100)

# With offset for pagination
limit(gt(PeRatio, 0), 50, offset=100)
```

### Error Expressions

When the LLM cannot interpret the query, it returns error expressions:

```python
error("non-finance")           # Not a financial query
error("insufficient metrics")  # Unknown metrics requested
error("unable to compute")     # Computation not supported
error("empty llm response")    # LLM returned empty
error("malformed expression")  # Invalid syntax
```

## Compiler Module

### Location
`compiler/` - Standalone module for expression compilation

### Components

#### `compiler/lexer.py`
Tokenizes Pythonic expressions into tokens.

**Tokens:**
- `IDENTIFIER` - Metric names, function names
- `NUMBER` - Numeric literals (int, float, scientific)
- `STRING` - String literals in quotes
- `LPAREN`, `RPAREN` - Parentheses
- `COMMA` - Argument separators
- `EQUALS` - Keyword argument assignment
- `EOF` - End of input

#### `compiler/ast.py`
Custom AST node definitions for diagnostics.

**Node Types:**
- `FilterNode` - Base class for all filter expressions
- `ComparisonNode` - gt, lt, geq, leq, neq
- `LogicalNode` - and, or, not
- `ArithmeticNode` - add, sub, mul, div
- `MetricNode` - Column references
- `NumberNode` - Numeric constants
- `OrderByNode` - Sorting specification
- `LimitNode` - Result limiting
- `ErrorNode` - Error expressions

#### `compiler/parser.py`
Recursive descent parser building custom AST.

**Features:**
- Validates syntax
- Builds typed AST nodes
- Provides error context (position, expected tokens)
- Handles optional keyword arguments

#### `compiler/codegen.py`
Generates parameterized DuckDB SQL.

**Features:**
- Converts AST to SQL WHERE clauses
- Maps grammar column names to parquet column names
- Generates parameterized queries (prevents injection)
- Handles query modifiers (ORDER BY, LIMIT)

#### `compiler/compiler.py`
Main entry point orchestrating lex→parse→codegen.

**API:**
```python
class SexpCompiler:
    def compile(self, expression: str) -> CompiledQuery
    
@dataclass
class CompiledQuery:
    sql: str                    # Parameterized SQL
    parameters: dict            # Query parameters
    tables: list[str]           # Tables to access
    query_type: str             # "screener", "chart", "combined"
```

### Usage

```python
from compiler import SexpCompiler

compiler = SexpCompiler()

# Compile expression
result = compiler.compile('gt(MarketCap, 1e9)')

# Returns:
# CompiledQuery(
#     sql="SELECT * FROM read_parquet('data/fundamentals.parquet') WHERE \"MarketCap\" > $1",
#     parameters={"$1": 1000000000.0},
#     tables=["fundamentals"],
#     query_type="screener"
# )
```

### Error Handling

The compiler raises `CompilationError` with context:

```python
class CompilationError(Exception):
    message: str
    position: int          # Character position in expression
    line: int              # Line number
    column: int            # Column number
    expected: list[str]    # Expected tokens
    context: str           # Surrounding code context
```

## Query Types

### Screener Queries
Filters fundamentals only, returns ticker list.

**SQL Pattern:**
```sql
SELECT Ticker, MarketCap, PeRatio, ...
FROM read_parquet('data/fundamentals.parquet')
WHERE <compiled_where_clause>
[ORDER BY <column>]
[LIMIT <count> OFFSET <offset>]
```

### Chart Queries
Retrieves time-series data for specific tickers and date range.

**SQL Pattern:**
```sql
SELECT *
FROM read_parquet('data/{tickers}/*.parquet', hive_partitioning=1)
WHERE Datetime BETWEEN $start_date AND $end_date
```

### Combined Queries
Two-phase query:
1. Filter tickers from fundamentals
2. Fetch time-series for filtered tickers only

## Security

### Injection Prevention
- All values are parameterized (`$1`, `$2`, etc.)
- Column names are validated against whitelist
- No string interpolation in generated SQL

### Safety Guard
- Local Qwen Guard model checks all prompts
- Blocks injection attempts (SQL keywords, special characters)
- Categorizes unsafe content

## Dependencies

### Core
- Python 3.14+
- duckdb - Database engine
- openai - OpenRouter API client
- transformers - Guard model inference
- sexpdata - Legacy s-expression support (migration period)

### Data
- pandas - Data manipulation
- pyarrow - Parquet I/O
- numpy - Numerical operations

### CLI/UX
- rich - Terminal formatting
- python-dotenv - Environment management

### Development
- promptfoo - LLM evaluation
- pytest - Testing
- jupyter - Data analysis

## Configuration

### Environment Variables
```bash
OPENROUTER_API=sk-...           # OpenRouter API key
TOKENIZERS_PARALLELISM=false    # Disable HF parallelism warning
```

### File Paths
- `data/fundamentals.parquet` - Static company data
- `data/<TICKER>/` - Time-series data directories

### Default Parameters
- Date range: Last 30 days (`-1 month` to `now`)
- Query timeout: 30 seconds
- Result limit: 1000 rows (configurable)

## API Integration

### Filter Model (OpenRouter)
- Model: `qwen/qwen3-14b`
- Temperature: 0 (deterministic)
- Max tokens: 4096

### Guard Model (Local)
- Model: `Qwen/Qwen3Guard-Gen-0.6B`
- Device: Auto (CUDA/MPS/CPU)
- Max tokens: 128

## Testing

### Evaluation
- Test cases in `eval.jsonc` (100+ examples)
- Categories: simple, simple_arithmetic, medium, hard, error_handling
- Metrics: Accuracy, syntax validity, semantic correctness

### Compiler Testing
- Unit tests for lexer, parser, codegen
- Integration tests with actual DuckDB queries
- Property-based testing for expression generation

## Migration Notes

### From S-expressions to Pythonic
Old: `(gt MarketCap 1e9)`  
New: `gt(MarketCap, 1e9)`

Key differences:
- Commas separate arguments
- Operators remain terse
- Parentheses required for all function calls
- No implicit operators

## Future Enhancements

### Grammar Extensions
- Date/time operators: `after()`, `before()`, `between_dates()`
- Aggregation functions: `avg()`, `sum()`, `max()`, `min()`
- Window functions: `rolling_avg()`, `change_over()`
- String operations: `contains()`, `starts_with()`

### Performance
- Query result caching
- Materialized views for common filters
- Column statistics for query optimization

### Features
- Real-time data streaming
- WebSocket API for live updates
- User query history and saved filters

## Development

### Setup
```bash
# Install dependencies
pip install -e ".[dev]"

# Generate synthetic data
python scripts/synthetic.py

# Run CLI
python main.py
```

### Adding New Metrics
1. Update `models/prompts.py` columns list
2. Update `compiler/codegen.py` column mapping
3. Regenerate synthetic data to include new columns
4. Add test cases to `eval.jsonc`

## License

[License information]
