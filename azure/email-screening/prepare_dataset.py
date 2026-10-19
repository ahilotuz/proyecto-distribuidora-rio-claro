import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

LABELS = {"supplier_invoice", "not_invoice"}
SPLITS = {"train", "test"}
EXPECTED_COUNTS = {
    ("train", "supplier_invoice"): 50,
    ("train", "not_invoice"): 50,
    ("test", "supplier_invoice"): 10,
    ("test", "not_invoice"): 10,
}


def load_examples(path: Path) -> list[dict[str, Any]]:
    examples = []
    seen_ids = set()
    seen_texts = set()
    with path.open(encoding="utf-8") as dataset_file:
        for line_number, line in enumerate(dataset_file, start=1):
            try:
                example = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"Invalid JSON on line {line_number}: {error.msg}"
                ) from error
            if not isinstance(example, dict):
                raise ValueError(f"Line {line_number} must contain a JSON object.")

            example_id = example.get("id")
            split = example.get("split")
            label = example.get("label")
            text = example.get("text")
            if not isinstance(example_id, str) or not example_id:
                raise ValueError(f"Line {line_number} has no valid id.")
            if example_id in seen_ids:
                raise ValueError(f"Duplicate example id: {example_id}")
            if split not in SPLITS:
                raise ValueError(f"Example {example_id} has an invalid split.")
            if label not in LABELS:
                raise ValueError(f"Example {example_id} has an invalid label.")
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"Example {example_id} has no text.")
            if text in seen_texts:
                raise ValueError(f"Duplicate example text: {example_id}")

            example["text"] = text.strip()
            examples.append(example)
            seen_ids.add(example_id)
            seen_texts.add(text)

    counts = Counter((example["split"], example["label"]) for example in examples)
    if counts != Counter(EXPECTED_COUNTS):
        raise ValueError(
            "Dataset class/split counts do not match expected counts: "
            f"{dict(counts)}"
        )
    return examples


def export_examples(examples: list[dict[str, Any]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.csv"
    with manifest_path.open("w", encoding="utf-8", newline="") as manifest_file:
        writer = csv.DictWriter(
            manifest_file,
            fieldnames=("id", "split", "label", "language", "filename"),
        )
        writer.writeheader()
        for example in examples:
            filename = f"{example['id']}.txt"
            text_path = output_dir / example["split"] / filename
            text_path.parent.mkdir(parents=True, exist_ok=True)
            text_path.write_text(f"{example['text']}\n", encoding="utf-8")
            writer.writerow(
                {
                    "id": example["id"],
                    "split": example["split"],
                    "label": example["label"],
                    "language": example.get("language", ""),
                    "filename": f"{example['split']}/{filename}",
                }
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and export synthetic Azure Language examples."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(__file__).parent / "data" / "synthetic_examples.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("build/synthetic-language-dataset"),
    )
    args = parser.parse_args()

    examples = load_examples(args.input)
    export_examples(examples, args.output)
    print(
        f"Validated and exported {len(examples)} examples to "
        f"{args.output.resolve()}."
    )


if __name__ == "__main__":
    main()
