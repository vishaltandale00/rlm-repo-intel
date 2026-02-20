from rlm_repo_intel.evaluation.pr_eval import extract_issue_refs, parse_pr_diff_files


def test_parse_pr_diff_files_extracts_unique_b_side_paths():
    diff = """\
diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1 @@
-print(\"hello\")
+print(\"hi\")
diff --git a/src/new.py b/src/new.py
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/src/new.py
diff --git a/src/app.py b/src/app.py
index 2222222..4444444 100644
--- a/src/app.py
+++ b/src/app.py
"""

    files = parse_pr_diff_files(diff)

    assert files == ["src/app.py", "src/new.py"]


def test_extract_issue_refs_deduplicates_and_preserves_order():
    text = "Fixes #12 and closes #33. Also related to #12 and #101."

    refs = extract_issue_refs(text)

    assert refs == [12, 33, 101]
