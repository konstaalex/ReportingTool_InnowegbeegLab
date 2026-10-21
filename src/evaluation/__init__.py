"""Evaluation harness for the Innoweegbeg drafting/verification pipeline
(UC2-UC4). Scores any condition's output against a frozen gold set using
structural proxies for RAGAS/FActScore faithfulness, ALCE citation
precision/recall, and AttributionBench/PaperTrail injected-error catch
rate -- see the "Evaluation Approach - Supporting Resources" doc for the
literature these mirror, and metrics.py's module docstring for exactly
what is and isn't tested before UC2 exists.
"""
