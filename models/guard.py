from transformers import AutoModelForCausalLM, AutoTokenizer
import re
import dataclasses
from enum import Enum, auto


safe_pattern = r"Safety: (Safe|Unsafe|Controversial)"
category_pattern = r"(Violent|Non-violent Illegal Acts|Sexual Content or Sexual Acts|PII|Suicide & Self-Harm|Unethical Acts|Politically Sensitive Topics|Copyright Violation|Jailbreak|None)"
injection_patterns = ["select ", "drop ", "union ", "insert ", "delete ", "--", ";", "or 1=1"]


class SafetyLabel(Enum):
    Safe = auto()
    Unsafe = auto()
    Controversial = auto()

    @classmethod
    def from_str(cls, value: str):
        match value.lower():
            case "safe":
                return cls.Safe
            case "controversial":
                return cls.Controversial
            case _:
                return cls.Unsafe


@dataclasses.dataclass
class Response:
    """
    wrapper class for tuple["safety_label", "categories"]
    """

    safety_label: SafetyLabel
    categories: list[str]

    def __iter__(self):
        yield self.safety_label
        yield self.categories


class GuardModel:
    def __init__(self, model_name: str = "Qwen/Qwen3Guard-Gen-0.6B") -> None:
        self.model_name = model_name
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForCausalLM.from_pretrained(  # type: ignore
            model_name, dtype="auto", device_map="auto"
        )

    def __call__(self, prompt: str) -> Response:
        if any(p in prompt.lower() for p in injection_patterns):
            return Response(SafetyLabel.Unsafe, ["Jailbreak"])

        messages = [{"role": "user", "content": prompt}]
        text = self.tokenizer.apply_chat_template(messages, tokenize=False)
        model_inputs = self.tokenizer([text], return_tensors="pt").to(self.model.device)
        generated_ids = self.model.generate(**model_inputs, max_new_tokens=128)
        output_ids = generated_ids[0][len(model_inputs.input_ids[0]) :].tolist()
        content = self.tokenizer.decode(output_ids, skip_special_tokens=True)
        safe_label, categories = extract_label_and_categories(content)
        safe_label = safe_label or "Unknown"
        categories = categories or ["None"]
        return Response(SafetyLabel.from_str(safe_label), categories)


def extract_label_and_categories(prompt: str) -> tuple[str | None, list[str]]:
    safe_label_match = re.search(safe_pattern, prompt)
    label = safe_label_match.group(1) if safe_label_match else None
    categories = re.findall(category_pattern, prompt)
    return label, categories
