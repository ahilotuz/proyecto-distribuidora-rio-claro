import tempfile
import unittest
from pathlib import Path

from prepare_dataset import export_examples, load_examples

DATASET_PATH = (
    Path(__file__).parents[1] / "data" / "synthetic_examples.jsonl"
)


class TestSyntheticDataset(unittest.TestCase):
    def test_dataset_has_balanced_training_and_holdout_examples(self):
        examples = load_examples(DATASET_PATH)

        self.assertEqual(len(examples), 120)
        self.assertEqual(
            sum(
                example["split"] == "train"
                and example["label"] == "supplier_invoice"
                for example in examples
            ),
            50,
        )
        self.assertEqual(
            sum(
                example["split"] == "test"
                and example["label"] == "not_invoice"
                for example in examples
            ),
            10,
        )

    def test_export_writes_text_documents_and_label_manifest(self):
        examples = load_examples(DATASET_PATH)

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            export_examples(examples, output_dir)

            self.assertEqual(
                len(list((output_dir / "train").glob("*.txt"))),
                100,
            )
            self.assertEqual(
                len(list((output_dir / "test").glob("*.txt"))),
                20,
            )
            manifest = (output_dir / "manifest.csv").read_text(encoding="utf-8")
            self.assertIn("invoice_train_001,train,supplier_invoice", manifest)
            self.assertIn("other_test_010,test,not_invoice", manifest)


if __name__ == "__main__":
    unittest.main()
