# Eval report

> P1 baseline — targets are expected to fail; recorded as measured; see PRD §11.3

## Run

- Profile: `full`; splits: dev, test, heldout; renderings: native, csv, scan, photo, rot90
- Sampling: `{"first": null, "fractions": {"csv": 1.0, "native": 1.0, "photo": 1.0, "rot90": 1.0, "scan": 1.0}}`
- Adapter: `pipeline_v0` version `1`
- Scorer: `engine_floor_v0` version `1`
- Gate mode: `none`; git SHA: `76c5d3105da42d7612ec65662fdffb14a5bbf197`
- Corpus dev: fingerprint `4d74df440a314e93a95565182c6cf8e48839687676f6955911461971661d592c`, seed 20261119, 240 reports
- Corpus test: fingerprint `8c62689178abad8418792394a8ea9d3dbf18e29569207a468874971c111572b8`, seed 20261119, 80 reports
- Corpus heldout: fingerprint `43f10b1c51016d1a5fb368fbabb06b05af2603610cf33bc6abe9b9667ee82d94`, seed 61700529, 80 reports

## Metrics (M1-M7 per split and rendering)

| Split | Rendering | Metric | Target | Value | Count | 95% CI | Result |
|---|---|---|---|---|---|---|---|
| dev | native | M1 silent-error rate | <= 0.5% | 23.97% | 1468/6124 | 22.92% to 25.06% | FAIL |
| dev | native | M2 critical recall | = 100% | 77.00% | 77/100 | 67.85% to 84.16% | FAIL |
| dev | native | M3 row accuracy before review | >= 92% | 47.04% | 3371/7166 | 45.89% to 48.20% | FAIL |
| dev | native | M4 false-critical rate | <= 0.5% | 0.28% | 17/6124 | 0.17% to 0.44% | PASS |
| dev | native | M5 headline on confirmed | >= 98% | 78.85% | 179/227 | 73.09% to 83.66% | FAIL |
| dev | native | M6 normal → ready | >= 90% | 0.00% | 0/49 | 0.00% to 7.27% | FAIL |
| dev | native | M7 review burden | <= 15% | 41.36% | 2502/6050 | 40.12% to 42.60% | FAIL |
| dev | csv | M1 silent-error rate | <= 0.5% | 36.55% | 231/632 | 32.89% to 40.38% | FAIL |
| dev | csv | M2 critical recall | = 100% | 83.33% | 10/12 | 55.20% to 95.30% | FAIL |
| dev | csv | M3 row accuracy before review | >= 92% | 59.59% | 382/641 | 55.75% to 63.33% | FAIL |
| dev | csv | M4 false-critical rate | <= 0.5% | 0.00% | 0/632 | 0.00% to 0.60% | PASS |
| dev | csv | M5 headline on confirmed | >= 98% | 92.31% | 12/13 | 66.69% to 98.63% | FAIL |
| dev | csv | M6 normal → ready | >= 90% | 50.00% | 1/2 | 9.45% to 90.55% | FAIL |
| dev | csv | M7 review burden | <= 15% | 5.50% | 35/636 | 3.98% to 7.56% | PASS |
| dev | scan | M1 silent-error rate | <= 1.5% | 27.06% | 1657/6124 | 25.96% to 28.18% | FAIL |
| dev | scan | M2 critical recall | = 100% | 66.00% | 66/100 | 56.28% to 74.54% | FAIL |
| dev | scan | M3 row accuracy before review | >= 80% | 39.15% | 2552/6519 | 37.97% to 40.34% | FAIL |
| dev | scan | M4 false-critical rate | <= 0.5% | 0.64% | 39/6124 | 0.47% to 0.87% | FAIL |
| dev | scan | M5 headline on confirmed | >= 98% | 83.26% | 189/227 | 77.86% to 87.55% | FAIL |
| dev | scan | M6 normal → ready | info | 0.00% | 0/49 | 0.00% to 7.27% | info |
| dev | scan | M7 review burden | info | 44.42% | 2361/5315 | 43.09% to 45.76% | info |
| dev | photo | M1 silent-error rate | <= 1.5% | 43.39% | 2657/6124 | 42.15% to 44.63% | FAIL |
| dev | photo | M2 critical recall | = 100% | 50.00% | 50/100 | 40.38% to 59.62% | FAIL |
| dev | photo | M3 row accuracy before review | >= 70% | 21.14% | 1445/6834 | 20.19% to 22.13% | FAIL |
| dev | photo | M4 false-critical rate | <= 0.5% | 1.83% | 112/6124 | 1.52% to 2.20% | FAIL |
| dev | photo | M5 headline on confirmed | >= 98% | 68.35% | 270/395 | 63.61% to 72.75% | FAIL |
| dev | photo | M6 normal → ready | info | 3.50% | 5/143 | 1.50% to 7.92% | info |
| dev | photo | M7 review burden | info | 56.82% | 2731/4806 | 55.42% to 58.22% | info |
| dev | rot90 | M1 silent-error rate | <= 1.5% | 11.47% | 32/279 | 8.24% to 15.74% | FAIL |
| dev | rot90 | M2 critical recall | = 100% | 0.00% | 0/3 | 0.00% to 56.15% | FAIL |
| dev | rot90 | M3 row accuracy before review | >= 80% | 0.00% | 0/282 | 0.00% to 1.34% | FAIL |
| dev | rot90 | M4 false-critical rate | <= 0.5% | 0.00% | 0/279 | 0.00% to 1.36% | PASS |
| dev | rot90 | M5 headline on confirmed | >= 98% | 9.09% | 1/11 | 1.62% to 37.74% | FAIL |
| dev | rot90 | M6 normal → ready | info | 0.00% | 0/4 | 0.00% to 48.99% | info |
| dev | rot90 | M7 review burden | info | 66.67% | 2/3 | 20.77% to 93.85% | info |
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
| test | scan | M1 silent-error rate | <= 1.5% | 24.32% | 490/2015 | 22.49% to 26.24% | FAIL |
| test | scan | M2 critical recall | = 100% | 88.89% | 32/36 | 74.69% to 95.59% | FAIL |
| test | scan | M3 row accuracy before review | >= 80% | 40.95% | 873/2132 | 38.88% to 43.05% | FAIL |
| test | scan | M4 false-critical rate | <= 0.5% | 0.40% | 8/2015 | 0.20% to 0.78% | PASS |
| test | scan | M5 headline on confirmed | >= 98% | 86.84% | 66/76 | 77.45% to 92.69% | FAIL |
| test | scan | M6 normal → ready | info | 6.25% | 1/16 | 1.11% to 28.33% | info |
| test | scan | M7 review burden | info | 45.17% | 800/1771 | 42.87% to 47.50% | info |
| test | photo | M1 silent-error rate | <= 1.5% | 43.52% | 877/2015 | 41.37% to 45.70% | FAIL |
| test | photo | M2 critical recall | = 100% | 55.56% | 20/36 | 39.58% to 70.46% | FAIL |
| test | photo | M3 row accuracy before review | >= 70% | 24.72% | 564/2282 | 22.99% to 26.53% | FAIL |
| test | photo | M4 false-critical rate | <= 0.5% | 1.19% | 24/2015 | 0.80% to 1.77% | FAIL |
| test | photo | M5 headline on confirmed | >= 98% | 77.27% | 102/132 | 69.41% to 83.59% | FAIL |
| test | photo | M6 normal → ready | info | 4.26% | 2/47 | 1.17% to 14.25% | info |
| test | photo | M7 review burden | info | 53.18% | 845/1589 | 50.72% to 55.62% | info |
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
| dev | native | 317 | 35 | 1116 |
| dev | csv | 226 | 0 | 5 |
| dev | scan | 396 | 57 | 1204 |
| dev | photo | 599 | 83 | 1975 |
| dev | rot90 | 0 | 1 | 31 |
| test | native | 84 | 11 | 317 |
| test | csv | 0 | 0 | 0 |
| test | scan | 112 | 17 | 361 |
| test | photo | 172 | 27 | 678 |
| test | rot90 | 0 | 0 | 61 |
| heldout | native | 137 | 17 | 375 |
| heldout | csv | 37 | 0 | 2 |
| heldout | scan | 112 | 19 | 643 |
| heldout | photo | 141 | 20 | 820 |
| heldout | rot90 | 0 | 0 | 15 |

## Dev and test diagnostics (non-gating)

#### dev / native

- M2-post 73.00% (73/100); M5-pre 60.35% (137/227); M5-oracle 93.39% (212/227); M5-oracle-clinical 100.00% (227/227); R-NR 70.43% (131/186)
- Recall 81.78% (5008/6124); precision 82.78% (5008/6050)
- Invented rows by decoy kind: none=893, interpretive=49, header_id=29, end_of_report=24, phone=20, tollfree=12, page_x_of_y=10, header_demographic=5
- Omitted rows by GT feature: glued_flag=609, technology_column=516, form_style=176, method_line=176, method_under_name=167, paediatric=161, sex_labelled_range=131, si=96, two_per_line=95, serology=54, serology_report=54, flag_column=52, titre=45, interpretive_table=26, mixed_specimen=21, urine_section=21, csf=14, csf_report=14, cumulative=8, plain_table=8, age_labelled_range=7, qualifier=6, urine_only=6, flag_first=5, interpretive_range=4, indian_grouping=3, method_column=2, none=2, qualitative_result=2, range_absent=1, unit_in_brackets=1, unitless_count=1, unlabelled_multivalue=1, western_grouping=1
- Headline confusion (GT>confirmed): abnormal>abnormal=85, normal>normal=51, critical>critical=43, normal>unknown=17, abnormal>unknown=15, critical>unknown=9, abnormal>normal=3, critical>abnormal=3, normal>abnormal=1
- Pre-review confusion (GT>product): abnormal>abnormal=95, normal>unknown=55, critical>critical=39, critical>abnormal=14, normal>abnormal=8, abnormal>critical=4, abnormal>unknown=4, normal>critical=3, normal>normal=3, critical>unknown=2
- Job status: ok=227

| Family | M3 | Count |
|---|---|---|
| G01 | 5.19% | 22/424 |
| G02 | 88.24% | 330/374 |
| G03 | 37.37% | 148/396 |
| G04 | 0.00% | 0/352 |
| G05 | 68.47% | 404/590 |
| G06 | 16.49% | 62/376 |
| G07 | 31.16% | 91/292 |
| G08 | 76.90% | 243/316 |
| G09 | 56.96% | 131/230 |
| G10 | 64.38% | 150/233 |
| IN-A | 95.27% | 383/402 |
| IN-B | 0.00% | 0/1028 |
| IN-C | 0.85% | 4/473 |
| IN-D | 28.27% | 54/191 |
| IN-E | 91.29% | 587/643 |
| UK-A | 95.85% | 393/410 |
| US-A | 84.63% | 369/436 |

Top error signatures:

1. `range` × 563
2. `unit` × 397
3. `flag` × 138
4. `name` × 129
5. `value+flag` × 100
6. `unit+flag` × 98
7. `range+flag` × 54
8. `unit+range+flag` × 27
9. `unit+range` × 21
10. `name+value+unit+range+flag` × 20
11. `value` × 17
12. `name+range+flag` × 14
13. `name+value+range+flag` × 13
14. `value+unit+range+flag` × 11
15. `name+flag` × 8
16. `name+value+unit+range` × 8
17. `name+unit` × 4
18. `name+unit+range+flag` × 4
19. `value+range` × 4
20. `value+range+flag` × 4

#### dev / csv

- M2-post 83.33% (10/12); M5-pre 84.62% (11/13); M5-oracle 100.00% (13/13); M5-oracle-clinical 100.00% (13/13); R-NR 75.00% (6/8)
- Recall 99.21% (627/632); precision 98.58% (627/636)
- Invented rows by decoy kind: csv_metadata=9
- Omitted rows by GT feature: decimal_comma=5
- Headline confusion (GT>confirmed): abnormal>abnormal=5, critical>critical=5, normal>normal=2, normal>abnormal=1
- Pre-review confusion (GT>product): abnormal>abnormal=5, critical>critical=5, normal>abnormal=2, normal>normal=1
- Job status: ok=13

| Family | M3 | Count |
|---|---|---|
| GEN-CSV | 59.59% | 382/641 |

Top error signatures:

1. `range` × 217
2. `range+flag` × 15
3. `flag` × 4
4. `name` × 4
5. `name+range+flag` × 4
6. `name+flag` × 1

#### dev / scan

- M2-post 61.00% (61/100); M5-pre 45.81% (104/227); M5-oracle 93.39% (212/227); M5-oracle-clinical 100.00% (227/227); R-NR 57.53% (107/186)
- Recall 80.34% (4920/6124); precision 92.57% (4920/5315)
- Invented rows by decoy kind: none=333, interpretive=38, header_demographic=17, address=6, page_x_of_y=1
- Omitted rows by GT feature: two_per_line=238, colon_line=222, paediatric=207, glued_flag=180, form_style=173, method_line=173, flag_column=135, sex_labelled_range=128, si=108, method_column=77, serology=73, serology_report=73, cumulative=67, titre=45, technology_column=41, mixed_specimen=34, method_under_name=33, flag_first=32, csf_report=31, plain_table=27, unlabelled_multivalue=26, csf=25, urine_section=24, interpretive_table=22, unit_in_brackets=20, header_unit=18, qualitative_result=17, age_labelled_range=13, none=12, qualifier=12, unitless_count=8, urine_only=8, index_line=6, range_absent=6, inline_unit=5, interpretive_range=3, western_grouping=2, indian_grouping=1
- Headline confusion (GT>confirmed): abnormal>abnormal=98, normal>normal=50, critical>critical=41, normal>abnormal=11, critical>abnormal=8, normal>unknown=8, abnormal>unknown=5, critical>unknown=4, critical>normal=2
- Pre-review confusion (GT>product): abnormal>abnormal=76, normal>unknown=40, critical>critical=27, critical>abnormal=22, normal>abnormal=19, abnormal>unknown=14, abnormal>critical=13, normal>critical=9, critical>unknown=6, normal>normal=1
- Job status: ok=227

| Family | M3 | Count |
|---|---|---|
| G01 | 55.50% | 212/382 |
| G02 | 15.03% | 58/386 |
| G03 | 67.01% | 260/388 |
| G04 | 0.00% | 0/246 |
| G05 | 52.33% | 315/602 |
| G06 | 5.25% | 20/381 |
| G07 | 0.63% | 2/317 |
| G08 | 51.96% | 172/331 |
| G09 | 38.30% | 90/235 |
| G10 | 41.25% | 106/257 |
| IN-A | 53.14% | 220/414 |
| IN-B | 18.84% | 101/536 |
| IN-C | 50.53% | 143/283 |
| IN-D | 0.00% | 0/195 |
| IN-E | 49.85% | 343/688 |
| UK-A | 60.19% | 257/427 |
| US-A | 56.10% | 253/451 |

Top error signatures:

1. `unit` × 550
2. `name` × 392
3. `range` × 343
4. `value+flag` × 262
5. `unit+flag` × 112
6. `name+unit` × 108
7. `flag` × 88
8. `unit+range+flag` × 83
9. `range+flag` × 61
10. `name+range+flag` × 51
11. `value+range+flag` × 51
12. `value` × 49
13. `value+unit+range+flag` × 37
14. `unit+range` × 35
15. `value+unit+flag` × 35
16. `name+unit+range+flag` × 20
17. `name+flag` × 16
18. `name+value+range+flag` × 12
19. `value+unit+range` × 12
20. `value+range` × 8

#### dev / photo

- M2-post 45.00% (45/100); M5-pre 29.37% (116/395); M5-oracle 94.18% (372/395); M5-oracle-clinical 99.75% (394/395); R-NR 58.06% (108/186)
- Recall 66.88% (4096/6124); precision 85.23% (4096/4806)
- Invented rows by decoy kind: none=645, interpretive=37, header_demographic=9, page_x_of_y=8, address=4, date=3, phone=3, turnaround=1
- Omitted rows by GT feature: glued_flag=373, two_per_line=364, flag_column=284, colon_line=247, paediatric=240, sex_labelled_range=197, si=179, technology_column=156, method_column=150, form_style=149, method_line=149, method_under_name=113, plain_table=103, cumulative=102, serology_report=91, flag_first=85, csf_report=83, serology=75, header_unit=69, mixed_specimen=63, unit_in_brackets=56, titre=45, csf=43, urine_section=38, unlabelled_multivalue=27, qualifier=23, interpretive_table=22, qualitative_result=22, urine_only=18, age_labelled_range=17, none=12, range_absent=8, index_line=6, indian_grouping=6, inline_unit=6, unitless_count=6, interpretive_range=2, western_grouping=2
- Headline confusion (GT>confirmed): abnormal>abnormal=119, normal>normal=119, normal>unknown=35, critical>critical=32, abnormal>normal=26, abnormal>unknown=19, normal>abnormal=16, critical>abnormal=13, critical>normal=10, critical>unknown=6
- Pre-review confusion (GT>product): normal>unknown=102, abnormal>abnormal=81, abnormal>unknown=43, abnormal>critical=38, normal>abnormal=31, critical>unknown=25, normal>critical=25, critical>critical=23, critical>abnormal=13, normal>normal=12, abnormal>normal=2
- Job status: ok=377, empty=18

| Family | M3 | Count |
|---|---|---|
| G01 | 19.74% | 76/385 |
| G02 | 9.69% | 37/382 |
| G03 | 35.26% | 140/397 |
| G04 | 0.00% | 0/241 |
| G05 | 32.02% | 195/609 |
| G06 | 6.92% | 27/390 |
| G07 | 0.00% | 0/320 |
| G08 | 29.19% | 108/370 |
| G09 | 13.67% | 38/278 |
| G10 | 33.07% | 84/254 |
| IN-A | 32.62% | 137/420 |
| IN-B | 7.11% | 46/647 |
| IN-C | 19.22% | 69/359 |
| IN-D | 0.00% | 0/207 |
| IN-E | 38.45% | 263/684 |
| UK-A | 26.56% | 115/433 |
| US-A | 24.02% | 110/458 |

Top error signatures:

1. `value+unit+range+flag` × 370
2. `unit` × 310
3. `value+unit+range` × 288
4. `range` × 259
5. `name` × 226
6. `value+range` × 214
7. `value+range+flag` × 181
8. `unit+range+flag` × 121
9. `unit+flag` × 104
10. `value+flag` × 88
11. `unit+range` × 69
12. `flag` × 68
13. `name+unit` × 62
14. `range+flag` × 60
15. `name+range+flag` × 44
16. `name+value+unit+range` × 42
17. `value` × 25
18. `name+value+range` × 24
19. `value+unit+flag` × 19
20. `name+value+range+flag` × 17

#### dev / rot90

- M2-post 0.00% (0/3); M5-pre 18.18% (2/11); M5-oracle 81.82% (9/11); M5-oracle-clinical 100.00% (11/11); R-NR 0.00% (0/5)
- Recall 0.00% (0/279); precision 0.00% (0/3)
- Invented rows by decoy kind: none=3
- Omitted rows by GT feature: serology_report=16, glued_flag=15, two_per_line=15, serology=14, qualitative_result=6, si=6, titre=4, index_line=3
- Headline confusion (GT>confirmed): normal>unknown=5, abnormal>unknown=4, critical>unknown=1, normal>normal=1
- Pre-review confusion (GT>product): normal>unknown=5, abnormal>unknown=3, abnormal>abnormal=1, critical>unknown=1, normal>normal=1
- Job status: empty=9, ok=2

| Family | M3 | Count |
|---|---|---|
| G01 | 0.00% | 0/58 |
| G02 | 0.00% | 0/5 |
| G04 | 0.00% | 0/25 |
| G05 | 0.00% | 0/17 |
| G06 | 0.00% | 0/40 |
| G09 | 0.00% | 0/16 |
| G10 | 0.00% | 0/17 |
| IN-A | 0.00% | 0/18 |
| IN-B | 0.00% | 0/10 |
| IN-C | 0.00% | 0/32 |
| US-A | 0.00% | 0/44 |

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

- M2-post 88.89% (32/36); M5-pre 50.00% (38/76); M5-oracle 92.11% (70/76); M5-oracle-clinical 100.00% (76/76); R-NR 54.55% (30/55)
- Recall 82.08% (1654/2015); precision 93.39% (1654/1771)
- Invented rows by decoy kind: none=98, interpretive=12, header_demographic=6, address=1
- Omitted rows by GT feature: colon_line=77, paediatric=77, two_per_line=55, si=45, form_style=44, method_line=44, flag_column=40, glued_flag=39, serology_report=39, serology=32, sex_labelled_range=32, method_column=22, titre=20, cumulative=15, method_under_name=15, flag_first=12, plain_table=12, mixed_specimen=11, interpretive_table=10, technology_column=10, csf_report=8, urine_section=8, csf=7, qualitative_result=7, unlabelled_multivalue=6, header_unit=5, qualifier=5, unit_in_brackets=4, urine_only=4, age_labelled_range=3, range_absent=3, interpretive_range=2, index_line=1, none=1, western_grouping=1
- Headline confusion (GT>confirmed): abnormal>abnormal=31, normal>normal=18, critical>critical=17, normal>abnormal=3, normal>unknown=3, abnormal>unknown=2, abnormal>normal=1, critical>unknown=1
- Pre-review confusion (GT>product): abnormal>abnormal=25, normal>unknown=14, critical>critical=11, normal>abnormal=7, abnormal>unknown=6, critical>abnormal=5, abnormal>critical=3, critical>unknown=2, normal>normal=2, normal>critical=1
- Job status: ok=76

| Family | M3 | Count |
|---|---|---|
| G01 | 52.17% | 84/161 |
| G02 | 23.75% | 38/160 |
| G03 | 60.51% | 95/157 |
| G04 | 0.00% | 0/67 |
| G05 | 62.36% | 111/178 |
| G06 | 5.10% | 5/98 |
| G07 | 0.00% | 0/77 |
| G08 | 47.37% | 45/95 |
| G09 | 48.21% | 27/56 |
| G10 | 37.98% | 49/129 |
| IN-A | 41.60% | 52/125 |
| IN-B | 22.13% | 27/122 |
| IN-C | 47.62% | 70/147 |
| IN-D | 0.00% | 0/79 |
| IN-E | 51.49% | 104/202 |
| UK-A | 60.71% | 85/140 |
| US-A | 58.27% | 81/139 |

Top error signatures:

1. `unit` × 176
2. `range` × 145
3. `name` × 98
4. `value+flag` × 82
5. `unit+flag` × 38
6. `flag` × 30
7. `range+flag` × 29
8. `name+unit` × 28
9. `value+range+flag` × 25
10. `unit+range+flag` × 22
11. `unit+range` × 16
12. `name+range+flag` × 14
13. `value+unit+flag` × 13
14. `value+unit+range+flag` × 11
15. `name+flag` × 10
16. `value` × 10
17. `name+value+unit+range+flag` × 9
18. `name+value+range+flag` × 8
19. `name+unit+range+flag` × 7
20. `value+range` × 4

#### test / photo

- M2-post 55.56% (20/36); M5-pre 30.30% (40/132); M5-oracle 95.45% (126/132); M5-oracle-clinical 100.00% (132/132); R-NR 47.27% (26/55)
- Recall 65.61% (1322/2015); precision 83.20% (1322/1589)
- Invented rows by decoy kind: none=237, interpretive=18, header_demographic=5, phone=3, address=2, page_x_of_y=2
- Omitted rows by GT feature: paediatric=106, method_column=105, flag_column=100, glued_flag=94, colon_line=88, two_per_line=85, method_under_name=82, sex_labelled_range=71, si=60, flag_first=53, serology_report=44, form_style=39, method_line=39, cumulative=37, technology_column=37, plain_table=35, serology=34, titre=20, header_unit=16, csf_report=15, mixed_specimen=15, csf=11, interpretive_table=10, urine_section=10, qualitative_result=8, unit_in_brackets=8, unlabelled_multivalue=7, qualifier=6, urine_only=6, age_labelled_range=4, indian_grouping=4, inline_unit=4, index_line=3, interpretive_range=3, none=3, range_absent=2
- Headline confusion (GT>confirmed): abnormal>abnormal=44, normal>normal=44, critical>critical=14, normal>unknown=11, abnormal>normal=7, normal>abnormal=4, critical>unknown=3, abnormal>unknown=2, critical>abnormal=2, critical>normal=1
- Pre-review confusion (GT>product): normal>unknown=42, abnormal>abnormal=30, abnormal>critical=13, normal>abnormal=11, abnormal>unknown=9, critical>abnormal=9, critical>critical=7, critical>unknown=4, normal>critical=3, normal>normal=3, abnormal>normal=1
- Job status: ok=127, empty=5

| Family | M3 | Count |
|---|---|---|
| G01 | 34.32% | 58/169 |
| G02 | 15.57% | 26/167 |
| G03 | 38.31% | 59/154 |
| G04 | 0.00% | 0/66 |
| G05 | 45.11% | 83/184 |
| G06 | 7.92% | 8/101 |
| G07 | 0.00% | 0/88 |
| G08 | 26.42% | 28/106 |
| G09 | 26.15% | 17/65 |
| G10 | 34.13% | 43/126 |
| IN-A | 15.87% | 20/126 |
| IN-B | 7.43% | 11/148 |
| IN-C | 11.57% | 25/216 |
| IN-D | 0.00% | 0/81 |
| IN-E | 52.02% | 103/198 |
| UK-A | 18.75% | 27/144 |
| US-A | 39.16% | 56/143 |

Top error signatures:

1. `range` × 109
2. `unit` × 108
3. `value+unit+range+flag` × 77
4. `value+flag` × 65
5. `value+unit+range` × 63
6. `name` × 54
7. `value+range` × 53
8. `value+range+flag` × 39
9. `unit+flag` × 35
10. `flag` × 25
11. `unit+range+flag` × 25
12. `range+flag` × 19
13. `name+unit` × 14
14. `unit+range` × 12
15. `name+value+unit+range` × 10
16. `name+range+flag` × 8
17. `name+value+unit+range+flag` × 8
18. `value` × 7
19. `value+unit+flag` × 7
20. `name+flag` × 6

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

## Heldout (aggregate only)

Aggregate metrics only. No per-report, per-family, per-analyte or confusion output exists for this split (plan 6.11).

| Split | Rendering | Metric | Target | Value | Count | 95% CI | Result |
|---|---|---|---|---|---|---|---|
| heldout | native | M1 silent-error rate | <= 0.5% | 25.93% | 529/2040 | 24.08% to 27.88% | FAIL |
| heldout | native | M2 critical recall | = 100% | 70.45% | 31/44 | 55.78% to 81.84% | FAIL |
| heldout | native | M3 row accuracy before review | >= 92% | 36.40% | 847/2327 | 34.47% to 38.37% | FAIL |
| heldout | native | M4 false-critical rate | <= 0.5% | 0.00% | 0/2040 | 0.00% to 0.19% | PASS |
| heldout | native | M5 headline on confirmed | >= 98% | 71.43% | 55/77 | 60.51% to 80.31% | FAIL |
| heldout | native | M6 normal → ready | >= 90% | 0.00% | 0/14 | 0.00% to 21.53% | FAIL |
| heldout | native | M7 review burden | <= 15% | 50.03% | 970/1939 | 47.80% to 52.25% | FAIL |
| heldout | csv | M1 silent-error rate | <= 0.5% | 29.32% | 39/133 | 22.25% to 37.55% | FAIL |
| heldout | csv | M2 critical recall | = 100% | n/a | 0/0 | n/a | n/a |
| heldout | csv | M3 row accuracy before review | >= 92% | 40.15% | 55/137 | 32.31% to 48.51% | FAIL |
| heldout | csv | M4 false-critical rate | <= 0.5% | 0.00% | 0/133 | 0.00% to 2.81% | PASS |
| heldout | csv | M5 headline on confirmed | >= 98% | 66.67% | 2/3 | 20.77% to 93.85% | FAIL |
| heldout | csv | M6 normal → ready | >= 90% | 0.00% | 0/2 | 0.00% to 65.76% | FAIL |
| heldout | csv | M7 review burden | <= 15% | 31.85% | 43/135 | 24.59% to 40.12% | FAIL |
| heldout | scan | M1 silent-error rate | <= 1.5% | 37.94% | 774/2040 | 35.86% to 40.07% | FAIL |
| heldout | scan | M2 critical recall | = 100% | 61.36% | 27/44 | 46.62% to 74.28% | FAIL |
| heldout | scan | M3 row accuracy before review | >= 80% | 28.58% | 620/2169 | 26.72% to 30.52% | FAIL |
| heldout | scan | M4 false-critical rate | <= 0.5% | 0.29% | 6/2040 | 0.13% to 0.64% | PASS |
| heldout | scan | M5 headline on confirmed | >= 98% | 72.73% | 56/77 | 61.88% to 81.42% | FAIL |
| heldout | scan | M6 normal → ready | info | 0.00% | 0/14 | 0.00% to 21.53% | info |
| heldout | scan | M7 review burden | info | 48.66% | 691/1420 | 46.07% to 51.26% | info |
| heldout | photo | M1 silent-error rate | <= 1.5% | 48.09% | 981/2040 | 45.93% to 50.26% | FAIL |
| heldout | photo | M2 critical recall | = 100% | 36.36% | 16/44 | 23.78% to 51.13% | FAIL |
| heldout | photo | M3 row accuracy before review | >= 70% | 15.61% | 358/2294 | 14.18% to 17.15% | FAIL |
| heldout | photo | M4 false-critical rate | <= 0.5% | 1.08% | 22/2040 | 0.71% to 1.63% | FAIL |
| heldout | photo | M5 headline on confirmed | >= 98% | 55.64% | 74/133 | 47.16% to 63.81% | FAIL |
| heldout | photo | M6 normal → ready | info | 0.00% | 0/43 | 0.00% to 8.20% | info |
| heldout | photo | M7 review burden | info | 60.87% | 787/1293 | 58.18% to 63.49% | info |
| heldout | rot90 | M1 silent-error rate | <= 1.5% | 34.09% | 15/44 | 21.88% to 48.86% | FAIL |
| heldout | rot90 | M2 critical recall | = 100% | n/a | 0/0 | n/a | n/a |
| heldout | rot90 | M3 row accuracy before review | >= 80% | 0.00% | 0/45 | 0.00% to 7.87% | FAIL |
| heldout | rot90 | M4 false-critical rate | <= 0.5% | 0.00% | 0/44 | 0.00% to 8.03% | PASS |
| heldout | rot90 | M5 headline on confirmed | >= 98% | 0.00% | 0/4 | 0.00% to 48.99% | FAIL |
| heldout | rot90 | M6 normal → ready | info | 0.00% | 0/2 | 0.00% to 65.76% | info |
| heldout | rot90 | M7 review burden | info | 100.00% | 1/1 | 20.65% to 100.00% | info |

| Rendering | Jobs | Legibility | M1 wrong | M1 invented | M1 omitted | Crashes by class |
|---|---|---|---|---|---|---|
| native | 77 | 100.00% | 137 | 17 | 375 | none |
| csv | 3 | 77.44% | 37 | 0 | 2 | none |
| scan | 77 | 92.65% | 112 | 19 | 643 | none |
| photo | 133 | 91.67% | 141 | 20 | 820 | none |
| rot90 | 4 | 29.55% | 0 | 0 | 15 | none |

## Gate

- Mode `none`: PASS

## Known v0 limitations

- Per-token OCR confidence, column-parser origin, the plausibility check and unread lines as rows are not surfaced by the current product, so v0 never flags on them and never covers an omission (plan 6.3.3).
- Qualitative and titre rows always carry no analyte key in the product; the name comparator runs in key-optional mode for them (plan 6.7).
- The v0 scorer does not model the ensemble, which can raise a no-anomaly report to abnormal; the effect is visible in M5-pre. P2 replaces this scorer with the real confirmed-rows scoring (plan 6.4).
- Legacy urine rows are never flagged for review: the product shows no uncertainty signal for them.
- The evaluation measures the offline, no-LLM configuration of the product.

## Runtime (non-deterministic)

- Wall time: 3604.78 s; OCR cache hits: 215, misses: 1795
- native: n=380, p50 8.125277189000371 s, p95 19.71105050799997 s
- csv: n=20, p50 0.05188147100034257 s, p95 0.20195719200000894 s
- scan: n=380, p50 11.15674464499989 s, p95 21.81233886100017 s
- photo: n=660, p50 4.840704280999489 s, p95 8.54938174400013 s
- rot90: n=19, p50 6.13720918799936 s, p95 22.608288229999744 s

