# Eval report

> P1 baseline — targets are expected to fail; recorded as measured; see PRD §11.3

## Run

- Profile: `ci`; splits: test; renderings: native, csv, scan, photo, rot90
- Sampling: `{"first": null, "fractions": {"csv": 1.0, "native": 1.0, "photo": 0.25, "rot90": 1.0, "scan": 0.25}}`
- Adapter: `pipeline_v0` version `1`
- Scorer: `engine_floor_v0` version `1`
- Gate mode: `ratchet`; git SHA: `7e92aebc5fcb787340d142220b63c271e2e7736c`
- Corpus test: fingerprint `8c62689178abad8418792394a8ea9d3dbf18e29569207a468874971c111572b8`, seed 20261119, 80 reports

## Metrics (M1-M7 per split and rendering)

| Split | Rendering | Metric | Target | Value | Count | 95% CI | Result |
|---|---|---|---|---|---|---|---|
| test | native | M1 silent-error rate | <= 0.5% | 20.45% | 412/2015 | 18.74% to 22.26% | FAIL |
| test | native | M2 critical recall | = 100% | 75.00% | 27/36 | 58.93% to 86.25% | FAIL |
| test | native | M3 row accuracy before review | >= 92% | 50.43% | 1169/2318 | 48.40% to 52.46% | FAIL |
| test | native | M4 false-critical rate | <= 0.5% | 0.20% | 4/2015 | 0.08% to 0.51% | PASS |
| test | native | M5 headline on confirmed | >= 98% | 82.89% | 63/76 | 72.90% to 89.72% | FAIL |
| test | native | M6 normal → ready | >= 90% | 6.25% | 1/16 | 1.11% to 28.33% | FAIL |
| test | native | M7 review burden | <= 15% | 38.98% | 780/2001 | 36.87% to 41.14% | FAIL |
| test | csv | M1 silent-error rate | <= 0.5% | 0.00% | 0/104 | 0.00% to 3.56% | PASS |
| test | csv | M2 critical recall | = 100% | 100.00% | 4/4 | 51.01% to 100.00% | PASS |
| test | csv | M3 row accuracy before review | >= 92% | 92.38% | 97/105 | 85.68% to 96.09% | PASS |
| test | csv | M4 false-critical rate | <= 0.5% | 0.00% | 0/104 | 0.00% to 3.56% | PASS |
| test | csv | M5 headline on confirmed | >= 98% | 100.00% | 4/4 | 51.01% to 100.00% | PASS |
| test | csv | M6 normal → ready | >= 90% | n/a | 0/0 | n/a | n/a |
| test | csv | M7 review burden | <= 15% | 12.38% | 13/105 | 7.38% to 20.04% | PASS |
| test | scan | M1 silent-error rate | <= 1.5% | 28.26% | 143/506 | 24.51% to 32.34% | FAIL |
| test | scan | M2 critical recall | = 100% | 91.67% | 11/12 | 64.61% to 98.51% | FAIL |
| test | scan | M3 row accuracy before review | >= 80% | 38.79% | 206/531 | 34.74% to 43.01% | FAIL |
| test | scan | M4 false-critical rate | <= 0.5% | 0.59% | 3/506 | 0.20% to 1.73% | FAIL |
| test | scan | M5 headline on confirmed | >= 98% | 85.00% | 17/20 | 63.96% to 94.76% | FAIL |
| test | scan | M6 normal → ready | info | 25.00% | 1/4 | 4.56% to 69.94% | info |
| test | scan | M7 review burden | info | 44.52% | 195/438 | 39.93% to 49.20% | info |
| test | photo | M1 silent-error rate | <= 1.5% | 45.85% | 232/506 | 41.56% to 50.21% | FAIL |
| test | photo | M2 critical recall | = 100% | 58.33% | 7/12 | 31.95% to 80.67% | FAIL |
| test | photo | M3 row accuracy before review | >= 70% | 25.05% | 139/555 | 21.62% to 28.81% | FAIL |
| test | photo | M4 false-critical rate | <= 0.5% | 1.38% | 7/506 | 0.67% to 2.83% | FAIL |
| test | photo | M5 headline on confirmed | >= 98% | 75.76% | 25/33 | 58.98% to 87.17% | FAIL |
| test | photo | M6 normal → ready | info | 10.00% | 1/10 | 1.79% to 40.42% | info |
| test | photo | M7 review burden | info | 47.70% | 187/392 | 42.81% to 52.65% | info |
| test | rot90 | M1 silent-error rate | <= 1.5% | 40.67% | 61/150 | 33.13% to 48.67% | FAIL |
| test | rot90 | M2 critical recall | = 100% | 0.00% | 0/5 | 0.00% to 43.45% | FAIL |
| test | rot90 | M3 row accuracy before review | >= 80% | 0.00% | 0/156 | 0.00% to 2.40% | FAIL |
| test | rot90 | M4 false-critical rate | <= 0.5% | 0.00% | 0/150 | 0.00% to 2.50% | PASS |
| test | rot90 | M5 headline on confirmed | >= 98% | 0.00% | 0/4 | 0.00% to 48.99% | FAIL |
| test | rot90 | M6 normal → ready | info | n/a | 0/0 | n/a | info |
| test | rot90 | M7 review burden | info | 100.00% | 6/6 | 60.97% to 100.00% | info |

## M1 components (wrong / invented / omitted)

| Split | Rendering | Wrong | Invented | Omitted |
|---|---|---|---|---|
| test | native | 84 | 11 | 317 |
| test | csv | 0 | 0 | 0 |
| test | scan | 43 | 7 | 93 |
| test | photo | 62 | 12 | 158 |
| test | rot90 | 0 | 0 | 61 |

## Dev and test diagnostics (non-gating)

#### test / native

- M2-post 72.22% (26/36); M5-pre 59.21% (45/76); M5-oracle 92.11% (70/76); M5-oracle-clinical 100.00% (76/76); R-NR 58.18% (32/55)
- Recall 84.27% (1698/2015); precision 84.86% (1698/2001)
- Invented rows by decoy kind: none=253, header_id=14, interpretive=11, end_of_report=10, tollfree=5, page_x_of_y=4, phone=4, header_demographic=2
- Omitted rows by GT feature: glued_flag=127, technology_column=115, method_under_name=81, paediatric=55, sex_labelled_range=55, form_style=44, method_line=44, si=29, serology=25, serology_report=25, flag_column=20, titre=20, two_per_line=12, interpretive_table=10, mixed_specimen=8, urine_section=8, indian_grouping=5, plain_table=5, qualifier=5, csf=4, csf_report=4, urine_only=4, cumulative=3, flag_first=3, age_labelled_range=2, interpretive_range=2, qualitative_result=1, unlabelled_multivalue=1, western_grouping=1
- Headline confusion (GT>confirmed): abnormal>abnormal=31, normal>normal=18, critical>critical=14, normal>unknown=6, critical>unknown=4, abnormal>unknown=3
- Pre-review confusion (GT>product): abnormal>abnormal=31, normal>unknown=20, critical>critical=13, critical>abnormal=5, abnormal>unknown=2, normal>abnormal=2, abnormal>critical=1, normal>critical=1, normal>normal=1
- Job status: ok=76

| Family | M3 | Count |
|---|---|---|
| G01 | 2.25% | 4/178 |
| G02 | 92.50% | 148/160 |
| G03 | 47.85% | 78/163 |
| G04 | 0.00% | 0/88 |
| G05 | 85.06% | 148/174 |
| G06 | 19.39% | 19/98 |
| G07 | 16.44% | 12/73 |
| G08 | 71.74% | 66/92 |
| G09 | 58.18% | 32/55 |
| G10 | 68.14% | 77/113 |
| IN-A | 95.83% | 115/120 |
| IN-B | 0.00% | 0/231 |
| IN-C | 1.72% | 4/233 |
| IN-D | 55.84% | 43/77 |
| IN-E | 91.15% | 175/192 |
| UK-A | 99.25% | 132/133 |
| US-A | 84.06% | 116/138 |

Top error signatures:

1. `range` × 206
2. `unit` × 119
3. `name` × 40
4. `flag` × 39
5. `range+flag` × 37
6. `unit+flag` × 20
7. `value+flag` × 20
8. `unit+range+flag` × 16
9. `unit+range` × 9
10. `value` × 5
11. `name+range+flag` × 4
12. `name+value+unit+range+flag` × 3
13. `name+unit+range+flag` × 2
14. `name+value+unit+range` × 2
15. `value+range+flag` × 2
16. `name+flag` × 1
17. `name+value+range+flag` × 1
18. `value+range` × 1
19. `value+unit+range` × 1
20. `value+unit+range+flag` × 1

#### test / csv

- M2-post 100.00% (4/4); M5-pre 100.00% (4/4); M5-oracle 100.00% (4/4); M5-oracle-clinical 100.00% (4/4); R-NR 100.00% (2/2)
- Recall 100.00% (104/104); precision 99.05% (104/105)
- Invented rows by decoy kind: csv_metadata=1
- Omitted rows by GT feature: none
- Headline confusion (GT>confirmed): abnormal>abnormal=2, critical>critical=2
- Pre-review confusion (GT>product): abnormal>abnormal=2, critical>critical=2
- Job status: ok=4

| Family | M3 | Count |
|---|---|---|
| GEN-CSV | 92.38% | 97/105 |

Top error signatures:

1. `flag` × 4
2. `name` × 3

#### test / scan

- M2-post 91.67% (11/12); M5-pre 65.00% (13/20); M5-oracle 90.00% (18/20); M5-oracle-clinical 100.00% (20/20); R-NR 53.85% (7/13)
- Recall 81.62% (413/506); precision 94.29% (413/438)
- Invented rows by decoy kind: none=23, header_demographic=2
- Omitted rows by GT feature: paediatric=51, colon_line=43, two_per_line=14, mixed_specimen=8, serology_report=8, serology=6, urine_section=6, method_column=5, cumulative=4, si=4, titre=4, urine_only=4, glued_flag=3, csf=2, csf_report=2, plain_table=2, qualitative_result=2, flag_column=1, sex_labelled_range=1
- Headline confusion (GT>confirmed): abnormal>abnormal=9, critical>critical=5, normal>normal=3, normal>abnormal=2, normal>unknown=1
- Pre-review confusion (GT>product): abnormal>abnormal=7, critical>critical=4, abnormal>unknown=2, normal>normal=2, normal>unknown=2, critical>unknown=1, normal>abnormal=1, normal>critical=1
- Job status: ok=20

| Family | M3 | Count |
|---|---|---|
| G01 | 46.43% | 26/56 |
| G02 | 22.22% | 18/81 |
| G05 | 50.00% | 5/10 |
| G06 | 3.12% | 1/32 |
| G07 | 0.00% | 0/29 |
| G08 | 45.61% | 26/57 |
| G09 | 50.00% | 3/6 |
| G10 | 37.04% | 10/27 |
| IN-A | 55.36% | 31/56 |
| IN-B | 56.25% | 9/16 |
| IN-D | 0.00% | 0/19 |
| IN-E | 51.00% | 51/100 |
| UK-A | 61.90% | 26/42 |

Top error signatures:

1. `range` × 43
2. `unit` × 43
3. `value+flag` × 28
4. `range+flag` × 18
5. `name` × 14
6. `value+range+flag` × 11
7. `unit+flag` × 10
8. `value` × 6
9. `name+unit` × 5
10. `unit+range+flag` × 5
11. `value+unit+flag` × 5
12. `name+unit+range+flag` × 4
13. `flag` × 3
14. `name+range+flag` × 3
15. `unit+range` × 3
16. `value+range` × 3
17. `name+value+unit+range+flag` × 1
18. `value+unit+range` × 1
19. `value+unit+range+flag` × 1

#### test / photo

- M2-post 58.33% (7/12); M5-pre 33.33% (11/33); M5-oracle 93.94% (31/33); M5-oracle-clinical 100.00% (33/33); R-NR 46.15% (6/13)
- Recall 67.79% (343/506); precision 87.50% (343/392)
- Invented rows by decoy kind: none=45, phone=3, header_demographic=1
- Omitted rows by GT feature: paediatric=61, colon_line=46, method_column=33, two_per_line=18, cumulative=10, sex_labelled_range=10, glued_flag=9, mixed_specimen=9, plain_table=9, header_unit=8, serology_report=8, urine_section=7, serology=6, si=6, urine_only=6, inline_unit=4, titre=4, technology_column=3, qualitative_result=2, age_labelled_range=1, csf=1, csf_report=1, qualifier=1, unlabelled_multivalue=1
- Headline confusion (GT>confirmed): abnormal>abnormal=13, normal>normal=8, critical>critical=4, abnormal>normal=2, normal>abnormal=2, normal>unknown=2, critical>abnormal=1, critical>unknown=1
- Pre-review confusion (GT>product): normal>unknown=7, abnormal>abnormal=6, abnormal>unknown=5, critical>critical=4, abnormal>critical=3, normal>abnormal=3, abnormal>normal=1, critical>abnormal=1, critical>unknown=1, normal>critical=1, normal>normal=1
- Job status: ok=31, empty=2

| Family | M3 | Count |
|---|---|---|
| G01 | 26.67% | 16/60 |
| G02 | 17.24% | 15/87 |
| G05 | 8.33% | 1/12 |
| G06 | 3.12% | 1/32 |
| G07 | 0.00% | 0/32 |
| G08 | 25.00% | 16/64 |
| G09 | 60.00% | 3/5 |
| G10 | 44.00% | 11/25 |
| IN-A | 21.43% | 12/56 |
| IN-B | 29.41% | 5/17 |
| IN-D | 0.00% | 0/19 |
| IN-E | 54.00% | 54/100 |
| UK-A | 10.87% | 5/46 |

Top error signatures:

1. `value+unit+range+flag` × 37
2. `range` × 29
3. `unit` × 23
4. `value+flag` × 16
5. `value+range` × 16
6. `value+unit+range` × 15
7. `value+range+flag` × 13
8. `range+flag` × 10
9. `name` × 9
10. `unit+range+flag` × 8
11. `unit+flag` × 5
12. `value` × 5
13. `flag` × 4
14. `name+value+unit+range+flag` × 3
15. `name+range+flag` × 2
16. `name+unit+range+flag` × 2
17. `name+value+unit+range` × 2
18. `name+unit` × 1
19. `name+value+range` × 1
20. `name+value+range+flag` × 1

#### test / rot90

- M2-post 0.00% (0/5); M5-pre 0.00% (0/4); M5-oracle 100.00% (4/4); M5-oracle-clinical 100.00% (4/4); R-NR 0.00% (0/4)
- Recall 0.00% (0/150); precision 0.00% (0/6)
- Invented rows by decoy kind: none=4, phone=2
- Omitted rows by GT feature: flag_column=33, flag_first=33, paediatric=28, plain_table=28, sex_labelled_range=2
- Headline confusion (GT>confirmed): abnormal>unknown=2, critical>unknown=2
- Pre-review confusion (GT>product): abnormal>unknown=2, critical>unknown=2
- Job status: empty=2, ok=2

| Family | M3 | Count |
|---|---|---|
| G01 | 0.00% | 0/29 |
| G03 | 0.00% | 0/38 |
| G06 | 0.00% | 0/40 |
| IN-E | 0.00% | 0/49 |

## Gate

- Mode `none`: PASS

## Known v0 limitations

- Per-token OCR confidence, column-parser origin, the plausibility check and unread lines as rows are not surfaced by the current product, so v0 never flags on them and never covers an omission (plan 6.3.3).
- Qualitative and titre rows always carry no analyte key in the product; the name comparator runs in key-optional mode for them (plan 6.7).
- The v0 scorer does not model the ensemble, which can raise a no-anomaly report to abnormal; the effect is visible in M5-pre. P2 replaces this scorer with the real confirmed-rows scoring (plan 6.4).
- Legacy urine rows are never flagged for review: the product shows no uncertainty signal for them.
- The evaluation measures the offline, no-LLM configuration of the product.

## Runtime (non-deterministic)

- Wall time: 3426.98 s; OCR cache hits: 11, misses: 194
- native: n=76, p50 74.2744958190001 s, p95 178.55128027199999 s
- csv: n=4, p50 0.04748881299929053 s, p95 0.056025446000148804 s
- scan: n=20, p50 70.02022024400003 s, p95 153.70459882399973 s
- photo: n=33, p50 40.331083514000056 s, p95 111.01631830499991 s
- rot90: n=4, p50 99.3054975519999 s, p95 135.88466942099967 s

