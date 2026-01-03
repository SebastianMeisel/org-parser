# test_filesearch.py
#
# Run:
#   python -m unittest -v
#
# This test suite is tailored to your CURRENT API:
#   un_quote_string, resolve_include, is_include, update_block_state,
#   should_skip_header_line, preamble_decision, read_with_includes
#
# It also avoids brittle expectations about trailing blank lines: a file ending
# with "\n" does NOT necessarily yield an extra "" line in Python iteration.

import tempfile
import unittest
from pathlib import Path

import filesearch as m  # <-- your module name


class TestFileSearch(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # ---------- helpers ----------
    def write(self, rel: str, content: str) -> Path:
        """
        Write `content` to a file relative to the temporary test directory.

        Returns:
            The absolute Path to the written file.
        """
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    # ---------- un_quote_string ----------
    def test_un_quote_string_double_quotes(self):
        self.assertEqual(m.un_quote_string('"file.org"'), "file.org")

    def test_un_quote_string_single_quotes(self):
        self.assertEqual(m.un_quote_string("'file.org'"), "file.org")

    def test_un_quote_string_no_quotes(self):
        self.assertEqual(m.un_quote_string("file.org"), "file.org")

    def test_un_quote_string_keeps_inner_whitespace_but_strips_outer(self):
        # Outer quotes removed; inner whitespace preserved except for strip() after slicing
        self.assertEqual(m.un_quote_string('"  file.org  "'), "file.org")

    # ---------- resolve_include ----------
    def test_resolve_include_relative_without_colon(self):
        main = self.write("main.org", "")
        resolved = m.resolve_include("#+INCLUDE child.org", main)
        self.assertEqual(resolved, (self.root / "child.org").resolve())

    def test_resolve_include_relative_with_colon_and_quotes(self):
        main = self.write("dir/main.org", "")
        resolved = m.resolve_include('#+INCLUDE: "child.org"', main)
        self.assertEqual(resolved, (self.root / "dir/child.org").resolve())

    def test_resolve_include_ignores_case_and_whitespace_in_line_parsing(self):
        # resolve_include itself doesn't regex-match; it assumes the line is include-ish.
        main = self.write("dir/main.org", "")
        resolved = m.resolve_include('   #+include:   "child.org"  ', main)
        self.assertEqual(resolved, (self.root / "dir/child.org").resolve())

    # ---------- is_include ----------
    def test_is_include_basic(self):
        self.assertTrue(m.is_include("#+INCLUDE foo.org"))

    def test_is_include_case_and_whitespace(self):
        self.assertTrue(m.is_include("   #+include: foo.org"))

    def test_is_include_non_include(self):
        self.assertFalse(m.is_include("#+TITLE: Hello"))
        self.assertFalse(m.is_include("not an include"))

    # ---------- update_block_state ----------
    def test_update_block_state_enters_and_leaves_example(self):
        state = False
        state = m.update_block_state("#+begin_example", state)
        self.assertTrue(state)
        state = m.update_block_state("inside", state)
        self.assertTrue(state)
        state = m.update_block_state("#+end_example", state)
        self.assertFalse(state)

    def test_update_block_state_enters_and_leaves_src(self):
        state = False
        state = m.update_block_state("  #+BEGIN_SRC python", state)
        self.assertTrue(state)
        state = m.update_block_state("print('hi')", state)
        self.assertTrue(state)
        state = m.update_block_state("  #+END_SRC", state)
        self.assertFalse(state)

    def test_update_block_state_ignores_non_verbatim_blocks(self):
        state = False
        state = m.update_block_state("#+begin_quote", state)
        self.assertFalse(state)
        state = m.update_block_state("#+end_quote", state)
        self.assertFalse(state)

    # ---------- should_skip_header_line ----------
    def test_should_skip_header_line_true(self):
        self.assertTrue(m.should_skip_header_line("#+TITLE: X"))
        self.assertTrue(m.should_skip_header_line("  #+author: Y"))
        self.assertTrue(m.should_skip_header_line("#+OPTIONS: toc:nil"))
        self.assertTrue(m.should_skip_header_line("#+DATE: 2025-01-01"))

    def test_should_skip_header_line_false(self):
        self.assertFalse(m.should_skip_header_line("#+LANGUAGE: de"))
        self.assertFalse(m.should_skip_header_line("#+PROPERTY: header-args :results output"))
        self.assertFalse(m.should_skip_header_line("not a header line"))
        self.assertFalse(m.should_skip_header_line("#+TITLE"))  # missing ':' => not a keyword line

    # ---------- preamble_decision ----------
    def test_preamble_decision_blank_line(self):
        skip, still = m.preamble_decision("")
        self.assertTrue(skip)
        self.assertTrue(still)

    def test_preamble_decision_skippable_header_line(self):
        skip, still = m.preamble_decision("#+TITLE: Hello")
        self.assertTrue(skip)
        self.assertTrue(still)

    def test_preamble_decision_first_content_ends_preamble(self):
        skip, still = m.preamble_decision("* Heading")
        self.assertFalse(skip)
        self.assertFalse(still)

    def test_preamble_decision_non_skipped_header_ends_preamble(self):
        # By design: a non-skipped #+KEY: line counts as "content start"
        skip, still = m.preamble_decision("#+LANGUAGE: de")
        self.assertFalse(skip)
        self.assertFalse(still)

    # ---------- read_with_includes (integration-ish tests) ----------
    def test_read_with_includes_expands_depth_first_and_skips_included_preamble(self):
        # Arrange files:
        # main -> includes redundant
        # redundant -> includes example
        #
        # Included file preambles:
        # - example: TITLE + blank should be skipped
        # - redundant: TITLE/AUTHOR + blank should be skipped
        self.write(
            "example.org",
            "\n".join([
                "#+TITLE: Example (skip)",
                "",
                "EXAMPLE-L1",
                "EXAMPLE-L2",
            ]) + "\n",
        )
        self.write(
            "redundant.org",
            "\n".join([
                "#+TITLE: Redundant (skip)",
                "#+AUTHOR: Someone (skip)",
                "",
                "REDUNDANT-L1",
                "#+INCLUDE: example.org",
                "REDUNDANT-L2",
            ]) + "\n",
        )
        main = self.write(
            "main.org",
            "\n".join([
                "#+TITLE: Main (keep; root)",
                "MAIN-L1",
                "#+INCLUDE: redundant.org",
                "MAIN-L2",
            ]) + "\n",
        )

        got = list(m.read_with_includes(main))

        # No trailing "" expectations; just actual yielded lines.
        expected = [
            "#+TITLE: Main (keep; root)",
            "MAIN-L1",
            "REDUNDANT-L1",
            "EXAMPLE-L1",
            "EXAMPLE-L2",
            "REDUNDANT-L2",
            "MAIN-L2",
        ]
        self.assertEqual(got, expected)

    def test_read_with_includes_does_not_expand_inside_verbatim_blocks(self):
        self.write("child.org", "CHILD-L1\nCHILD-L2\n")
        main = self.write(
            "main.org",
            "\n".join([
                "TOP",
                "#+begin_example",
                "#+INCLUDE: child.org",
                "#+end_example",
                "BOTTOM",
            ]) + "\n",
        )

        got = list(m.read_with_includes(main))
        expected = [
            "TOP",
            "#+begin_example",
            "#+INCLUDE: child.org",  # yielded literally, not expanded
            "#+end_example",
            "BOTTOM",
        ]
        self.assertEqual(got, expected)

    def test_read_with_includes_preamble_ends_on_non_skipped_header(self):
        # In included file, TITLE is skipped; LANGUAGE is not skipped and ends preamble.
        # After preamble ends, AUTHOR is no longer skipped and should appear.
        self.write(
            "inc.org",
            "\n".join([
                "#+TITLE: Inc title (skip)",
                "#+LANGUAGE: de (not skipped; ends preamble and is yielded)",
                "#+AUTHOR: Inc author (now content; should be yielded)",
                "INC-L1",
            ]) + "\n",
        )
        main = self.write("main.org", "#+INCLUDE: inc.org\n")

        got = list(m.read_with_includes(main))
        expected = [
            "#+LANGUAGE: de (not skipped; ends preamble and is yielded)",
            "#+AUTHOR: Inc author (now content; should be yielded)",
            "INC-L1",
        ]
        self.assertEqual(got, expected)

    def test_read_with_includes_preserves_blank_lines_in_body(self):
        # Blank lines after preamble should be preserved.
        self.write(
            "inc.org",
            "\n".join([
                "#+TITLE: skip",
                "",
                "L1",
                "",
                "L2",
            ]) + "\n",
        )
        main = self.write("main.org", "#+INCLUDE: inc.org\n")

        got = list(m.read_with_includes(main))
        expected = [
            "L1",
            "",    # body blank line preserved
            "L2",
        ]
        self.assertEqual(got, expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
