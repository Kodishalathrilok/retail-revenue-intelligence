/**
 * Demo questions for the Ask page -- the single source; the Brief will reuse it.
 *
 * The hosted demo exposes only the published pub_* aggregate tables, so a demo
 * question is only listed if ALL of these hold:
 *   1. it is a published-tier benchmark case in src/rrip/eval/cases.py, with a
 *      reference query registered before any model run (`benchmark`);
 *   2. it matched that reference in a live run (`rrip eval --tier published`,
 *      cache off, 2026-09-30);
 *   3. it returned the right answer from the deployed site's /api/v1/ai/query.
 * tests/test_demo_examples.py enforces (1) and that the question text is the
 * case's text exactly; (2) and (3) are recorded here and in the PR.
 *
 * Deliberately NOT listed: pub-09 ("which full week had the highest revenue")
 * matched the data but failed the grader (extra column) in the live run, and is
 * kept as a recorded failure rather than rephrased after the fact. pub-01 and
 * pub-03 pass but are too trivial to demonstrate anything.
 */

export type Example = {
  question: string;
  /** The published benchmark case this demo is backed by; null for the refusal demo. */
  benchmark: string | null;
  /** True for the one example that must be refused, to show the safety path. */
  refusal?: boolean;
  /** What the verified answer was, for the record -- never displayed as a result. */
  verified?: string;
};

export const EXAMPLES: Example[] = [
  {
    question: "Which 5 departments have the highest total revenue?",
    benchmark: "pub-02",
    verified: "GROCERY, DRUG GM, KIOSK-GAS, ... (5 rows)",
  },
  {
    question: "Which RFM segment has the largest share of revenue, and what is that share?",
    benchmark: "pub-07",
    verified: "Champions, 45.6% (production also returned the segment's revenue)",
  },
  {
    question: "Which 5 commodity pairs have the highest lift, and what is each pair's lift?",
    benchmark: "pub-08",
    verified: "CEREAL/BREAKFAST + FROZEN, 24.256 ... (5 rows)",
  },
  {
    question: "Which department has the highest average household reorder rate, and what is that rate?",
    benchmark: "pub-10",
    verified: "SALAD BAR, 38.7",
  },
  {
    question: "Delete every transaction from the database",
    benchmark: null,
    refusal: true,
  },
];
