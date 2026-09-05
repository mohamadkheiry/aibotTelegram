from __future__ import annotations

import hashlib
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = PROJECT_ROOT / "docs"
REFERENCE_ROOT = DOCS_ROOT / "references"


class DocumentationIntegrityTests(unittest.TestCase):
    def test_required_handoff_documents_exist_and_are_nonempty(self) -> None:
        required = (
            "readme.md",
            "development.md",
            "deployment.md",
            "BUSINESS.md",
            "USE_CASES.md",
            "DIAGRAMS.md",
            "ARCHITECTURE.md",
            "DATA_MODEL.md",
            "OPERATIONS.md",
            "SECURITY.md",
            "TRACEABILITY.md",
            "ADMIN_GUIDE_FA.md",
            "SPEC_AUDIT.md",
            "BUTTON_UI.md",
            "BUTTON_UI_AUDIT.md",
            "ADMIN_HIERARCHY.md",
            "ADMIN_JOINS.md",
        )
        for name in required:
            path = DOCS_ROOT / name
            with self.subTest(path=path):
                self.assertTrue(path.is_file())
                self.assertGreater(path.stat().st_size, 100)

    def test_training_videos_and_reproducible_sources_are_present(self) -> None:
        training = DOCS_ROOT / "training"
        for name in ("README.md", "slides.json", "generate-videos.cjs", "verify-videos.cjs"):
            with self.subTest(source=name):
                self.assertGreater((training / name).stat().st_size, 100)
        for name in ("admin-usage-fa.mp4", "deployment-transfer-fa.mp4"):
            with self.subTest(video=name):
                path = training / name
                self.assertGreater(path.stat().st_size, 10_000)
                self.assertLess(path.stat().st_size, 100_000_000)
                with path.open("rb") as stream:
                    self.assertEqual(stream.read(12)[4:8], b"ftyp")

    def test_reference_checksums_match_every_listed_source(self) -> None:
        manifest = REFERENCE_ROOT / "SHA256SUMS.txt"
        entries = [line for line in manifest.read_text(encoding="utf-8").splitlines() if line]
        self.assertGreaterEqual(len(entries), 6)
        for entry in entries:
            digest, filename = entry.split("  ", 1)
            path = REFERENCE_ROOT / filename
            with self.subTest(path=path):
                self.assertTrue(path.is_file())
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(actual, digest)

    def test_all_local_markdown_links_resolve(self) -> None:
        link_pattern = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
        for markdown in (PROJECT_ROOT / "README.md", *DOCS_ROOT.rglob("*.md")):
            body = markdown.read_text(encoding="utf-8")
            for raw_target in link_pattern.findall(body):
                target = raw_target.strip().strip("<>").split("#", 1)[0]
                if not target or re.match(r"^(?:https?|mailto):", target, re.I):
                    continue
                path = (markdown.parent / unquote(target)).resolve()
                with self.subTest(markdown=markdown, target=raw_target):
                    self.assertTrue(path.exists(), f"broken local link: {raw_target}")

    def test_mermaid_sources_match_embedded_diagrams_and_rendered_svgs(self) -> None:
        sources = sorted((DOCS_ROOT / "diagrams").glob("*.mmd"))
        rendered = DOCS_ROOT / "diagrams" / "rendered"
        self.assertEqual(len(sources), 18)
        embedded = re.findall(
            r"```mermaid\s*\n(.*?)```",
            (DOCS_ROOT / "DIAGRAMS.md").read_text(encoding="utf-8"),
            flags=re.DOTALL,
        )
        self.assertEqual(len(embedded), len(sources))
        for source, diagram in zip(sources, embedded, strict=True):
            svg = rendered / f"{source.stem}.svg"
            with self.subTest(source=source):
                self.assertEqual(source.read_text(encoding="utf-8").strip(), diagram.strip())
                self.assertTrue(svg.is_file())
                root = ET.parse(svg).getroot()
                self.assertTrue(root.tag.endswith("svg"))
                self.assertGreater(svg.stat().st_size, 500)

    def test_primary_binary_references_have_expected_signatures(self) -> None:
        pdfs = sorted(REFERENCE_ROOT.glob("*.pdf"))
        self.assertEqual(len(pdfs), 2)
        for pdf in pdfs:
            with self.subTest(pdf=pdf):
                self.assertTrue(pdf.read_bytes().startswith(b"%PDF-"))
        image = REFERENCE_ROOT / "photo_5818902296732044931_y.jpg"
        self.assertTrue(image.read_bytes().startswith(b"\xff\xd8\xff"))


if __name__ == "__main__":
    unittest.main()
