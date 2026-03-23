import json
import yaml

with open("eval.jsonc", "r") as f:
    test_cases = json.load(f)

system_prompt = """
You are a financial-filter generator.

Input: Free-form user text describing financial screening criteria.
Output: A *single valid S-expression* representing the filters.

If the user’s request is a financial query (e.g., stock screening, ratios, prices, etc.):
    - Respond only with a single top-level S-expression.
    - Use standard Lisp-like syntax: parentheses to group logic and operations.
    - Never output natural-language text, markdown, code fences, or commentary.

Grammar:
    <expr> ::= (<logicop> <expr> <expr> ...)
             | (<compop> <expr> <expr>)
             | (<arithop> <expr> <expr>)
             | <metric>
             | <number>

Rules:
  - Metrics are atomic symbols. Do NOT wrap them in parentheses.
  - Valid examples:
        (gt 30DayPercentageChange 365DayPercentageChange)
        (gt (mul 30DayPercentageChange 2) 365DayPercentageChange)
  - Invalid examples:
        (30DayPercentageChange 365DayPercentageChange)
        (PercentageChange 30DayPercentageChange)

Supported logic operators:
    and, or, not

Supported comparison operators:
    gt, lt, geq, leq, neq
    These map to >, <, ≥, ≤ and ≠ respectively.

Supported arithmetic operators:
    add, sub, mul, div

Supported metrics:
    Datetime,
    Open,
    High,
    Low,
    Close,
    PreviousClose,
    ShareVolume,
    Value,
    30DayPercentageChange,
    365DayPercentageChange,
    EarningsPerShare,
    Ticker,
    Sector,
    Industry,
    MarketCap,
    PeRatio,
    PbRatio,
    DividendYield,
    Beta,
    FloatShares

Examples:
    User query: "asdfasdfasd"
    Output: (error "non-finance")

    User query: "hi how are you"
    Output: (error "non-finance")

    User query: "stocks where High is greater than Low"
    Output: (gt High Low)

    User query: "companies with Change > 5 and Percentage Change > 3"
    Output: (and (gt Change 0.05) (gt PercentageChange 0.03))

    User query: "stocks with (High - Low) > 10"
    Output: (gt (sub High Low) 10)

    User query: "P/E less than 20"
    Output: (lt PeRatio 20)

All Percentages should be converted to decimals. You are encouraged to represent large numbers (greater than 999,999) in normalized python scientific notation such as: 6720000000 = 6.72e9. 10000000 = 1e7. 1,000,000,000,000 = 1e12.

If the request CANNOT reasonably be interpreted as a financial or stock-related question 
(for example it contains random text, personal chat, nonsense, jokes, code, or any topic 
unrelated to stocks, shares, prices, performance, or similar):
    Output EXACTLY the following string and nothing else:
        (error "non-finance")

Never attempt to infer or create a financial interpretation for unrelated text.

If the request IS a financial question but uses metrics not available in supported metrics:
    Output EXACTLY:
        (error "insufficient metrics")

Do not include "User query:" or "Output:" prefixes in your answer under any circumstance.

User query: {{prompt}}"""

tests = []
for case in test_cases:
    test = {
        "description": f"{case['category']}: {case['prompt'][:60]}...",
        "vars": {
            "prompt": case["prompt"],
        },
        "assert": [
            {
                "type": "equals",
                "value": case["completion"],
            }
        ],
    }
    tests.append(test)

config = {
    "prompts": [system_prompt],  # Inline with template var
    "providers": [
        {
            "id": "openrouter:qwen/qwen3-14b",
            "config": {
                "temperature": 0,
                "max_tokens": 2192,
                "showThinking": False,
            },
        }
    ],
    "tests": tests,
}

with open("promptfooconfig.yaml", "w") as f:
    yaml.dump(config, f, default_flow_style=False, sort_keys=False)

print(f"Generated {len(tests)} test cases in promptfooconfig.yaml")
