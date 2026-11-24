# Eval report

> P1 baseline — targets are expected to fail; recorded as measured; see PRD §11.3

## Run

- Profile: `full`; splits: dev, test, heldout; renderings: native, csv, scan, photo, rot90
- Sampling: `{"first": null, "fractions": {"csv": 1.0, "native": 1.0, "photo": 1.0, "rot90": 1.0, "scan": 1.0}}`
- Adapter: `pipeline_v0` version `1`
- Scorer: `engine_floor_v0` version `1`
- Gate mode: `none`; git SHA: `ddee5dbb12ce896d2cd62769ad6ed78d428c9ef3`
- Judge digest (matching, review simulator, metric definitions, targets, gate, job selection, adapters): `f94923b006105f93503e0da66d557e79ecd359cc4606045bb6027ab0b88f6bf0`
- Corpus dev: fingerprint `c61d5a55a1420d109ec034dd74863af5fb2ef82c911e517085d1b792230db7d4`, seed 20261119, 240 reports
- Corpus test: fingerprint `c79cdd7cb3ad4b00ca810dd6d31f732ea6e98caee717d21d6cd6f2f0f742073b`, seed 20261119, 80 reports
- Corpus heldout: fingerprint `aef3360861731fba2b7877338125b8d6f805a4ef2badf5fa2dd369f6cdfdba52`, seed 61700529, 80 reports

## Metrics (M1-M7 and M2_post per split and rendering)

M2 is PRD section 3 as written (flagged CRITICAL or marked needs_review, before confirmation). M2_post is the S3 final state: the confirmed row of a GT critical is scored CRITICAL (target 100%, ratcheted). M4-post (diagnostic) counts critical flags on confirmed rows whose GT is not critical.

| Split | Rendering | Metric | Target | Value | Count | 95% CI | Result |
|---|---|---|---|---|---|---|---|
| dev | native | M1 silent-error rate | <= 0.5% | 24.52% | 1518/6192 | 23.46% to 25.60% | FAIL |
| dev | native | M2 critical recall | = 100% | 72.28% | 73/101 | 62.85% to 80.07% | FAIL |
| dev | native | M2_post critical handling after confirmation (S3) | = 100% | 64.36% | 65/101 | 54.65% to 73.01% | FAIL |
| dev | native | M3 row accuracy before review | >= 92% | 47.68% | 3452/7240 | 46.53% to 48.83% | FAIL |
| dev | native | M4 false-critical rate | <= 0.5% | 0.27% | 17/6192 | 0.17% to 0.44% | PASS |
| dev | native | M5 headline on confirmed | >= 98% | 78.41% | 178/227 | 72.61% to 83.27% | FAIL |
| dev | native | M6 normal → ready | >= 90% | 0.00% | 0/47 | 0.00% to 7.56% | FAIL |
| dev | native | M7 review burden | <= 15% | 39.79% | 2426/6097 | 38.57% to 41.02% | FAIL |
| dev | csv | M1 silent-error rate | <= 0.5% | 36.55% | 231/632 | 32.89% to 40.38% | FAIL |
| dev | csv | M2 critical recall | = 100% | 83.33% | 10/12 | 55.20% to 95.30% | FAIL |
| dev | csv | M2_post critical handling after confirmation (S3) | = 100% | 83.33% | 10/12 | 55.20% to 95.30% | FAIL |
| dev | csv | M3 row accuracy before review | >= 92% | 59.59% | 382/641 | 55.75% to 63.33% | FAIL |
| dev | csv | M4 false-critical rate | <= 0.5% | 0.00% | 0/632 | 0.00% to 0.60% | PASS |
| dev | csv | M5 headline on confirmed | >= 98% | 92.31% | 12/13 | 66.69% to 98.63% | FAIL |
| dev | csv | M6 normal → ready | >= 90% | 50.00% | 1/2 | 9.45% to 90.55% | FAIL |
| dev | csv | M7 review burden | <= 15% | 5.50% | 35/636 | 3.98% to 7.56% | PASS |
| dev | scan | M1 silent-error rate | <= 1.5% | 27.07% | 1676/6192 | 25.97% to 28.19% | FAIL |
| dev | scan | M2 critical recall | = 100% | 72.28% | 73/101 | 62.85% to 80.07% | FAIL |
| dev | scan | M2_post critical handling after confirmation (S3) | = 100% | 64.36% | 65/101 | 54.65% to 73.01% | FAIL |
| dev | scan | M3 row accuracy before review | >= 80% | 39.47% | 2586/6551 | 38.30% to 40.66% | FAIL |
| dev | scan | M4 false-critical rate | <= 0.5% | 0.58% | 36/6192 | 0.42% to 0.80% | FAIL |
| dev | scan | M5 headline on confirmed | >= 98% | 81.06% | 184/227 | 75.46% to 85.62% | FAIL |
| dev | scan | M6 normal → ready | >= 90% | 0.00% | 0/47 | 0.00% to 7.56% | FAIL |
| dev | scan | M7 review burden | info | 43.75% | 2341/5351 | 42.42% to 45.08% | info |
| dev | photo | M1 silent-error rate | <= 1.5% | 44.41% | 2750/6192 | 43.18% to 45.65% | FAIL |
| dev | photo | M2 critical recall | = 100% | 49.50% | 50/101 | 39.95% to 59.09% | FAIL |
| dev | photo | M2_post critical handling after confirmation (S3) | = 100% | 45.54% | 46/101 | 36.17% to 55.24% | FAIL |
| dev | photo | M3 row accuracy before review | >= 70% | 20.72% | 1437/6936 | 19.78% to 21.69% | FAIL |
| dev | photo | M4 false-critical rate | <= 0.5% | 1.57% | 97/6192 | 1.29% to 1.91% | FAIL |
| dev | photo | M5 headline on confirmed | >= 98% | 69.62% | 275/395 | 64.91% to 73.95% | FAIL |
| dev | photo | M6 normal → ready | >= 90% | 2.65% | 4/151 | 1.03% to 6.61% | FAIL |
| dev | photo | M7 review burden | info | 56.87% | 2752/4839 | 55.47% to 58.26% | info |
| dev | rot90 | M1 silent-error rate | <= 1.5% | 27.56% | 78/283 | 22.68% to 33.04% | FAIL |
| dev | rot90 | M2 critical recall | = 100% | 0.00% | 0/3 | 0.00% to 56.15% | FAIL |
| dev | rot90 | M2_post critical handling after confirmation (S3) | = 100% | 0.00% | 0/3 | 0.00% to 56.15% | FAIL |
| dev | rot90 | M3 row accuracy before review | >= 80% | 0.00% | 0/286 | 0.00% to 1.33% | FAIL |
| dev | rot90 | M4 false-critical rate | <= 0.5% | 0.00% | 0/283 | 0.00% to 1.34% | PASS |
| dev | rot90 | M5 headline on confirmed | >= 98% | 18.18% | 2/11 | 5.14% to 47.70% | FAIL |
| dev | rot90 | M6 normal → ready | >= 90% | 0.00% | 0/4 | 0.00% to 48.99% | FAIL |
| dev | rot90 | M7 review burden | info | 75.00% | 3/4 | 30.06% to 95.44% | info |
| test | native | M1 silent-error rate | <= 0.5% | 20.71% | 417/2014 | 18.99% to 22.53% | FAIL |
| test | native | M2 critical recall | = 100% | 67.57% | 25/37 | 51.46% to 80.37% | FAIL |
| test | native | M2_post critical handling after confirmation (S3) | = 100% | 62.16% | 23/37 | 46.10% to 75.94% | FAIL |
| test | native | M3 row accuracy before review | >= 92% | 51.86% | 1202/2318 | 49.82% to 53.88% | FAIL |
| test | native | M4 false-critical rate | <= 0.5% | 0.15% | 3/2014 | 0.05% to 0.44% | PASS |
| test | native | M5 headline on confirmed | >= 98% | 80.26% | 61/76 | 69.96% to 87.66% | FAIL |
| test | native | M6 normal → ready | >= 90% | 6.67% | 1/15 | 1.19% to 29.82% | FAIL |
| test | native | M7 review burden | <= 15% | 37.00% | 740/2000 | 34.91% to 39.14% | FAIL |
| test | csv | M1 silent-error rate | <= 0.5% | 0.00% | 0/104 | 0.00% to 3.56% | PASS |
| test | csv | M2 critical recall | = 100% | 100.00% | 4/4 | 51.01% to 100.00% | PASS |
| test | csv | M2_post critical handling after confirmation (S3) | = 100% | 100.00% | 4/4 | 51.01% to 100.00% | PASS |
| test | csv | M3 row accuracy before review | >= 92% | 92.38% | 97/105 | 85.68% to 96.09% | PASS |
| test | csv | M4 false-critical rate | <= 0.5% | 0.00% | 0/104 | 0.00% to 3.56% | PASS |
| test | csv | M5 headline on confirmed | >= 98% | 100.00% | 4/4 | 51.01% to 100.00% | PASS |
| test | csv | M6 normal → ready | >= 90% | n/a | 0/0 | n/a | n/a |
| test | csv | M7 review burden | <= 15% | 12.38% | 13/105 | 7.38% to 20.04% | PASS |
| test | scan | M1 silent-error rate | <= 1.5% | 24.43% | 492/2014 | 22.60% to 26.35% | FAIL |
| test | scan | M2 critical recall | = 100% | 81.08% | 30/37 | 65.80% to 90.52% | FAIL |
| test | scan | M2_post critical handling after confirmation (S3) | = 100% | 72.97% | 27/37 | 57.02% to 84.60% | FAIL |
| test | scan | M3 row accuracy before review | >= 80% | 42.70% | 912/2136 | 40.61% to 44.81% | FAIL |
| test | scan | M4 false-critical rate | <= 0.5% | 0.25% | 5/2014 | 0.11% to 0.58% | PASS |
| test | scan | M5 headline on confirmed | >= 98% | 84.21% | 64/76 | 74.40% to 90.73% | FAIL |
| test | scan | M6 normal → ready | >= 90% | 6.67% | 1/15 | 1.19% to 29.82% | FAIL |
| test | scan | M7 review burden | info | 43.07% | 765/1776 | 40.79% to 45.39% | info |
| test | photo | M1 silent-error rate | <= 1.5% | 46.52% | 937/2014 | 44.35% to 48.71% | FAIL |
| test | photo | M2 critical recall | = 100% | 51.35% | 19/37 | 35.89% to 66.55% | FAIL |
| test | photo | M2_post critical handling after confirmation (S3) | = 100% | 45.95% | 17/37 | 31.04% to 61.62% | FAIL |
| test | photo | M3 row accuracy before review | >= 70% | 23.83% | 542/2274 | 22.13% to 25.63% | FAIL |
| test | photo | M4 false-critical rate | <= 0.5% | 0.89% | 18/2014 | 0.57% to 1.41% | FAIL |
| test | photo | M5 headline on confirmed | >= 98% | 74.24% | 98/132 | 66.17% to 80.94% | FAIL |
| test | photo | M6 normal → ready | >= 90% | 6.98% | 3/43 | 2.40% to 18.61% | FAIL |
| test | photo | M7 review burden | info | 52.10% | 808/1551 | 49.61% to 54.57% | info |
| test | rot90 | M1 silent-error rate | <= 1.5% | 50.68% | 74/146 | 42.66% to 58.67% | FAIL |
| test | rot90 | M2 critical recall | = 100% | 0.00% | 0/5 | 0.00% to 43.45% | FAIL |
| test | rot90 | M2_post critical handling after confirmation (S3) | = 100% | 0.00% | 0/5 | 0.00% to 43.45% | FAIL |
| test | rot90 | M3 row accuracy before review | >= 80% | 0.00% | 0/155 | 0.00% to 2.42% | FAIL |
| test | rot90 | M4 false-critical rate | <= 0.5% | 0.00% | 0/146 | 0.00% to 2.56% | PASS |
| test | rot90 | M5 headline on confirmed | >= 98% | 0.00% | 0/4 | 0.00% to 48.99% | FAIL |
| test | rot90 | M6 normal → ready | >= 90% | n/a | 0/0 | n/a | n/a |
| test | rot90 | M7 review burden | info | 100.00% | 9/9 | 70.09% to 100.00% | info |

## M1 components (wrong / invented / omitted)

| Split | Rendering | Wrong | Invented | Omitted |
|---|---|---|---|---|
| dev | native | 341 | 34 | 1143 |
| dev | csv | 226 | 0 | 5 |
| dev | scan | 426 | 56 | 1194 |
| dev | photo | 615 | 83 | 2052 |
| dev | rot90 | 0 | 1 | 77 |
| test | native | 88 | 11 | 318 |
| test | csv | 0 | 0 | 0 |
| test | scan | 115 | 17 | 360 |
| test | photo | 195 | 27 | 715 |
| test | rot90 | 0 | 0 | 74 |
| heldout | native | 144 | 15 | 368 |
| heldout | csv | 37 | 0 | 2 |
| heldout | scan | 114 | 16 | 588 |
| heldout | photo | 149 | 17 | 732 |
| heldout | rot90 | 0 | 0 | 8 |

## Dev and test diagnostics (non-gating)

#### dev / native

- M4-post 0.00% (0/6192); M5-pre 59.47% (135/227); M5-oracle 92.95% (211/227); M5-oracle-clinical 100.00% (227/227); R-NR 71.89% (133/185)
- Recall 81.54% (5049/6192); precision 82.81% (5049/6097)
- Invented rows by decoy kind: none=913, interpretive=53, header_id=25, end_of_report=20, phone=13, page_x_of_y=11, tollfree=9, header_demographic=4
- Omitted rows by GT feature: glued_flag=626, technology_column=531, method_under_name=185, form_style=169, method_line=169, paediatric=161, sex_labelled_range=134, two_per_line=97, si=95, serology=54, serology_report=54, flag_column=50, titre=45, interpretive_table=27, mixed_specimen=21, urine_section=21, csf=14, csf_report=14, cumulative=9, plain_table=7, age_labelled_range=6, flag_first=6, qualifier=6, urine_only=6, interpretive_range=4, indian_grouping=3, method_column=2, none=2, qualitative_result=2, header_unit=1, range_absent=1, unit_in_brackets=1, unitless_count=1, unlabelled_multivalue=1, western_grouping=1
- Headline confusion (GT>confirmed): abnormal>abnormal=85, normal>normal=52, critical>critical=41, normal>unknown=16, abnormal>unknown=15, critical>unknown=10, critical>abnormal=4, abnormal>normal=3, normal>abnormal=1
- Pre-review confusion (GT>product): abnormal>abnormal=93, normal>unknown=53, critical>critical=39, critical>abnormal=14, normal>abnormal=10, abnormal>unknown=6, abnormal>critical=4, normal>critical=3, normal>normal=3, critical>unknown=2
- Job status: ok=227

| Family | M3 | Count |
|---|---|---|
| G01 | 17.39% | 60/345 |
| G02 | 88.51% | 339/383 |
| G03 | 34.93% | 146/418 |
| G04 | 0.00% | 0/338 |
| G05 | 67.53% | 391/579 |
| G06 | 16.41% | 65/396 |
| G07 | 25.43% | 74/291 |
| G08 | 76.90% | 243/316 |
| G09 | 58.58% | 140/239 |
| G10 | 64.38% | 150/233 |
| IN-A | 95.27% | 383/402 |
| IN-B | 0.00% | 0/1055 |
| IN-C | 0.58% | 3/516 |
| IN-D | 37.19% | 74/199 |
| IN-E | 90.78% | 591/651 |
| UK-A | 95.97% | 405/422 |
| US-A | 84.90% | 388/457 |

Top error signatures:

1. `range` × 521
2. `unit` × 434
3. `flag` × 148
4. `name` × 132
5. `value+flag` × 85
6. `unit+flag` × 79
7. `range+flag` × 44
8. `unit+range` × 26
9. `value` × 24
10. `unit+range+flag` × 23
11. `name+value+unit+range+flag` × 19
12. `name+range+flag` × 13
13. `name+value+range+flag` × 11
14. `value+unit+range+flag` × 8
15. `name+flag` × 7
16. `name+value+unit+range` × 5
17. `name+unit+range+flag` × 4
18. `value+range+flag` × 4
19. `name+unit` × 3
20. `name+value+flag` × 2

#### dev / csv

- M4-post 0.00% (0/632); M5-pre 84.62% (11/13); M5-oracle 100.00% (13/13); M5-oracle-clinical 100.00% (13/13); R-NR 75.00% (6/8)
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

- M4-post 0.00% (0/6192); M5-pre 48.46% (110/227); M5-oracle 92.95% (211/227); M5-oracle-clinical 100.00% (227/227); R-NR 52.97% (98/185)
- Recall 80.62% (4992/6192); precision 93.29% (4992/5351)
- Invented rows by decoy kind: none=299, interpretive=36, header_demographic=18, address=6
- Omitted rows by GT feature: two_per_line=241, colon_line=240, paediatric=201, form_style=159, method_line=159, glued_flag=150, flag_column=148, sex_labelled_range=128, si=116, method_column=78, serology=73, serology_report=73, cumulative=63, titre=45, flag_first=37, technology_column=37, csf_report=36, mixed_specimen=34, unlabelled_multivalue=29, csf=28, method_under_name=25, urine_section=24, unit_in_brackets=22, interpretive_table=19, header_unit=18, qualitative_result=17, plain_table=15, age_labelled_range=12, range_absent=9, none=8, urine_only=8, qualifier=7, unitless_count=7, index_line=6, inline_unit=5, western_grouping=2
- Headline confusion (GT>confirmed): abnormal>abnormal=94, normal>normal=48, critical>critical=42, normal>abnormal=13, normal>unknown=8, critical>abnormal=7, abnormal>unknown=6, abnormal>normal=3, critical>normal=3, critical>unknown=3
- Pre-review confusion (GT>product): abnormal>abnormal=77, normal>unknown=42, critical>critical=32, normal>abnormal=18, abnormal>critical=14, abnormal>unknown=12, critical>abnormal=12, critical>unknown=11, normal>critical=8, normal>normal=1
- Job status: ok=226, empty=1

| Family | M3 | Count |
|---|---|---|
| G01 | 57.69% | 180/312 |
| G02 | 14.03% | 55/392 |
| G03 | 64.82% | 269/415 |
| G04 | 0.00% | 0/235 |
| G05 | 57.84% | 332/574 |
| G06 | 5.74% | 23/401 |
| G07 | 0.00% | 0/309 |
| G08 | 51.96% | 172/331 |
| G09 | 43.21% | 105/243 |
| G10 | 41.25% | 106/257 |
| IN-A | 53.14% | 220/414 |
| IN-B | 17.61% | 96/545 |
| IN-C | 52.60% | 162/308 |
| IN-D | 0.00% | 0/203 |
| IN-E | 47.87% | 337/704 |
| UK-A | 60.96% | 267/438 |
| US-A | 55.74% | 262/470 |

Top error signatures:

1. `unit` × 543
2. `name` × 406
3. `range` × 332
4. `value+flag` × 277
5. `unit+flag` × 113
6. `name+unit` × 106
7. `flag` × 91
8. `unit+range+flag` × 85
9. `range+flag` × 61
10. `value+range+flag` × 59
11. `name+range+flag` × 54
12. `value` × 51
13. `unit+range` × 41
14. `value+unit+range+flag` × 38
15. `value+unit+flag` × 34
16. `name+flag` × 22
17. `name+unit+range+flag` × 21
18. `name+value+range+flag` × 17
19. `name+value+flag` × 10
20. `name+value` × 8

#### dev / photo

- M4-post 0.00% (0/6192); M5-pre 30.89% (122/395); M5-oracle 93.42% (369/395); M5-oracle-clinical 99.49% (393/395); R-NR 51.89% (96/185)
- Recall 66.13% (4095/6192); precision 84.62% (4095/4839)
- Invented rows by decoy kind: none=682, interpretive=36, page_x_of_y=8, header_demographic=7, address=5, phone=3, date=2, turnaround=1
- Omitted rows by GT feature: glued_flag=371, two_per_line=367, flag_column=321, colon_line=269, paediatric=231, sex_labelled_range=202, si=185, technology_column=168, method_column=157, form_style=141, method_line=141, method_under_name=123, cumulative=118, csf_report=95, flag_first=95, serology_report=91, plain_table=84, serology=75, header_unit=73, mixed_specimen=63, unit_in_brackets=55, csf=49, titre=45, urine_section=38, unlabelled_multivalue=35, qualitative_result=22, interpretive_table=21, urine_only=18, age_labelled_range=15, qualifier=11, none=10, range_absent=9, inline_unit=7, index_line=6, indian_grouping=6, unitless_count=6, western_grouping=2, interpretive_range=1
- Headline confusion (GT>confirmed): normal>normal=128, abnormal>abnormal=114, normal>unknown=37, critical>critical=33, abnormal>normal=22, abnormal>unknown=20, normal>abnormal=16, critical>abnormal=12, critical>normal=7, critical>unknown=6
- Pre-review confusion (GT>product): normal>unknown=112, abnormal>abnormal=82, abnormal>unknown=38, abnormal>critical=34, normal>abnormal=33, critical>critical=28, normal>critical=24, critical>abnormal=15, critical>unknown=15, normal>normal=12, abnormal>normal=2
- Job status: ok=378, empty=17

| Family | M3 | Count |
|---|---|---|
| G01 | 16.09% | 51/317 |
| G02 | 7.87% | 31/394 |
| G03 | 36.12% | 151/418 |
| G04 | 0.00% | 0/236 |
| G05 | 33.05% | 195/590 |
| G06 | 6.81% | 28/411 |
| G07 | 0.00% | 0/324 |
| G08 | 29.19% | 108/370 |
| G09 | 12.80% | 37/289 |
| G10 | 33.07% | 84/254 |
| IN-A | 32.62% | 137/420 |
| IN-B | 5.99% | 40/668 |
| IN-C | 18.89% | 75/397 |
| IN-D | 0.00% | 0/215 |
| IN-E | 34.71% | 243/700 |
| UK-A | 31.01% | 138/445 |
| US-A | 24.39% | 119/488 |

Top error signatures:

1. `value+unit+range+flag` × 386
2. `unit` × 309
3. `value+unit+range` × 272
4. `range` × 265
5. `name` × 238
6. `value+range` × 203
7. `value+range+flag` × 162
8. `unit+range+flag` × 123
9. `unit+flag` × 100
10. `value+flag` × 81
11. `unit+range` × 76
12. `flag` × 75
13. `range+flag` × 68
14. `name+unit` × 61
15. `name+value+unit+range` × 46
16. `name+range+flag` × 40
17. `value` × 29
18. `name+value+range` × 25
19. `name+unit+range+flag` × 22
20. `value+unit+flag` × 19

#### dev / rot90

- M4-post 0.00% (0/283); M5-pre 18.18% (2/11); M5-oracle 81.82% (9/11); M5-oracle-clinical 100.00% (11/11); R-NR 0.00% (0/5)
- Recall 0.35% (1/283); precision 25.00% (1/4)
- Invented rows by decoy kind: none=3
- Omitted rows by GT feature: flag_column=51, serology_report=16, serology=14, glued_flag=10, two_per_line=10, sex_labelled_range=7, qualitative_result=6, si=4, titre=4, index_line=3
- Headline confusion (GT>confirmed): normal>unknown=5, abnormal>unknown=3, abnormal>abnormal=1, critical>unknown=1, normal>normal=1
- Pre-review confusion (GT>product): normal>unknown=5, abnormal>unknown=3, abnormal>abnormal=1, critical>unknown=1, normal>normal=1
- Job status: empty=8, ok=3

| Family | M3 | Count |
|---|---|---|
| G01 | 0.00% | 0/49 |
| G02 | 0.00% | 0/25 |
| G04 | 0.00% | 0/20 |
| G05 | 0.00% | 0/12 |
| G06 | 0.00% | 0/40 |
| G09 | 0.00% | 0/11 |
| G10 | 0.00% | 0/17 |
| IN-A | 0.00% | 0/18 |
| IN-B | 0.00% | 0/10 |
| IN-C | 0.00% | 0/32 |
| US-A | 0.00% | 0/52 |

Top error signatures:

1. `value+unit+range+flag` × 1

#### test / native

- M4-post 0.00% (0/2014); M5-pre 56.58% (43/76); M5-oracle 92.11% (70/76); M5-oracle-clinical 100.00% (76/76); R-NR 62.32% (43/69)
- Recall 84.21% (1696/2014); precision 84.80% (1696/2000)
- Invented rows by decoy kind: none=258, header_id=11, interpretive=11, end_of_report=8, page_x_of_y=6, phone=4, tollfree=4, header_demographic=2
- Omitted rows by GT feature: method_under_name=107, glued_flag=104, technology_column=104, paediatric=55, sex_labelled_range=52, form_style=48, method_line=48, si=29, serology=25, serology_report=25, titre=20, flag_column=15, interpretive_table=9, mixed_specimen=8, urine_section=8, csf=4, csf_report=4, indian_grouping=4, plain_table=4, urine_only=4, qualifier=3, age_labelled_range=2, cumulative=2, flag_first=2, interpretive_range=2, qualitative_result=1, two_per_line=1, unlabelled_multivalue=1
- Headline confusion (GT>confirmed): abnormal>abnormal=31, normal>normal=17, critical>critical=13, normal>unknown=6, critical>unknown=5, abnormal>unknown=3, normal>abnormal=1
- Pre-review confusion (GT>product): abnormal>abnormal=30, normal>unknown=18, critical>critical=12, critical>abnormal=6, normal>abnormal=4, abnormal>unknown=3, abnormal>critical=1, normal>critical=1, normal>normal=1
- Job status: ok=76

| Family | M3 | Count |
|---|---|---|
| G01 | 9.50% | 17/179 |
| G02 | 92.16% | 141/153 |
| G03 | 43.92% | 83/189 |
| G04 | 0.00% | 0/96 |
| G05 | 97.50% | 195/200 |
| G06 | 19.79% | 19/96 |
| G07 | 13.11% | 8/61 |
| G08 | 71.74% | 66/92 |
| G09 | 59.46% | 44/74 |
| G10 | 68.14% | 77/113 |
| IN-A | 96.09% | 123/128 |
| IN-B | 0.00% | 0/208 |
| IN-C | 1.63% | 4/246 |
| IN-D | 69.23% | 45/65 |
| IN-E | 89.27% | 158/177 |
| UK-A | 99.13% | 114/115 |
| US-A | 85.71% | 108/126 |

Top error signatures:

1. `range` × 203
2. `unit` × 99
3. `name` × 45
4. `flag` × 42
5. `range+flag` × 35
6. `unit+flag` × 25
7. `unit+range+flag` × 12
8. `name+range+flag` × 7
9. `value` × 7
10. `unit+range` × 6
11. `value+flag` × 5
12. `name+unit+range+flag` × 3
13. `name+flag` × 1
14. `name+value+range+flag` × 1
15. `name+value+unit+range+flag` × 1
16. `value+range` × 1
17. `value+range+flag` × 1

#### test / csv

- M4-post 0.00% (0/104); M5-pre 100.00% (4/4); M5-oracle 100.00% (4/4); M5-oracle-clinical 100.00% (4/4); R-NR 100.00% (2/2)
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

- M4-post 0.00% (0/2014); M5-pre 51.32% (39/76); M5-oracle 92.11% (70/76); M5-oracle-clinical 100.00% (76/76); R-NR 56.52% (39/69)
- Recall 82.13% (1654/2014); precision 93.13% (1654/1776)
- Invented rows by decoy kind: none=102, interpretive=13, header_demographic=6, address=1
- Omitted rows by GT feature: paediatric=76, colon_line=70, two_per_line=60, glued_flag=49, form_style=48, method_line=48, si=47, serology_report=39, flag_column=36, serology=32, sex_labelled_range=31, method_column=22, titre=20, plain_table=15, cumulative=13, method_under_name=13, mixed_specimen=11, technology_column=10, csf_report=9, flag_first=9, unlabelled_multivalue=8, urine_section=8, interpretive_table=7, qualitative_result=7, header_unit=6, csf=5, unit_in_brackets=4, urine_only=4, age_labelled_range=3, qualifier=3, range_absent=2, index_line=1, none=1
- Headline confusion (GT>confirmed): abnormal>abnormal=30, normal>normal=18, critical>critical=16, normal>abnormal=3, normal>unknown=3, abnormal>normal=2, abnormal>unknown=2, critical>abnormal=1, critical>unknown=1
- Pre-review confusion (GT>product): abnormal>abnormal=25, normal>unknown=16, critical>critical=13, abnormal>unknown=7, normal>abnormal=6, critical>abnormal=3, abnormal>critical=2, critical>unknown=2, normal>critical=1, normal>normal=1
- Job status: ok=76

| Family | M3 | Count |
|---|---|---|
| G01 | 54.82% | 91/166 |
| G02 | 19.35% | 30/155 |
| G03 | 69.02% | 127/184 |
| G04 | 0.00% | 0/68 |
| G05 | 58.29% | 123/211 |
| G06 | 5.21% | 5/96 |
| G07 | 0.00% | 0/66 |
| G08 | 47.37% | 45/95 |
| G09 | 46.67% | 35/75 |
| G10 | 37.98% | 49/129 |
| IN-A | 42.96% | 58/135 |
| IN-B | 21.62% | 24/111 |
| IN-C | 53.19% | 75/141 |
| IN-D | 0.00% | 0/68 |
| IN-E | 51.63% | 95/184 |
| UK-A | 61.98% | 75/121 |
| US-A | 61.07% | 80/131 |

Top error signatures:

1. `unit` × 167
2. `range` × 137
3. `name` × 91
4. `value+flag` × 58
5. `unit+flag` × 40
6. `range+flag` × 30
7. `unit+range+flag` × 29
8. `flag` × 27
9. `name+unit` × 27
10. `value` × 22
11. `value+range+flag` × 20
12. `unit+range` × 17
13. `name+range+flag` × 15
14. `value+unit+flag` × 12
15. `value+unit+range+flag` × 10
16. `name+value+unit+range+flag` × 9
17. `name+flag` × 8
18. `name+unit+range+flag` × 7
19. `name+value+range+flag` × 6
20. `value+unit` × 3

#### test / photo

- M4-post 0.00% (0/2014); M5-pre 32.58% (43/132); M5-oracle 93.94% (124/132); M5-oracle-clinical 100.00% (132/132); R-NR 47.83% (33/69)
- Recall 64.10% (1291/2014); precision 83.24% (1291/1551)
- Invented rows by decoy kind: none=232, interpretive=16, header_demographic=5, phone=3, address=2, page_x_of_y=2
- Omitted rows by GT feature: glued_flag=123, flag_column=116, paediatric=112, two_per_line=112, method_column=107, colon_line=96, sex_labelled_range=73, method_under_name=67, flag_first=64, si=55, form_style=46, method_line=46, serology_report=44, technology_column=42, plain_table=38, serology=34, cumulative=27, titre=20, csf_report=17, header_unit=15, mixed_specimen=15, csf=12, interpretive_table=10, urine_section=10, qualitative_result=8, qualifier=7, unlabelled_multivalue=7, urine_only=6, age_labelled_range=4, indian_grouping=4, inline_unit=4, interpretive_range=4, unit_in_brackets=4, index_line=3, none=3, range_absent=3, western_grouping=1
- Headline confusion (GT>confirmed): abnormal>abnormal=46, normal>normal=39, critical>critical=13, normal>unknown=9, abnormal>normal=7, critical>abnormal=6, normal>abnormal=6, critical>unknown=3, abnormal>unknown=2, critical>normal=1
- Pre-review confusion (GT>product): normal>unknown=35, abnormal>abnormal=28, abnormal>unknown=15, normal>abnormal=14, abnormal>critical=11, critical>critical=11, critical>abnormal=8, critical>unknown=4, normal>normal=4, abnormal>normal=1, normal>critical=1
- Job status: ok=130, empty=2

| Family | M3 | Count |
|---|---|---|
| G01 | 33.71% | 59/175 |
| G02 | 12.50% | 20/160 |
| G03 | 34.64% | 62/179 |
| G04 | 0.00% | 0/68 |
| G05 | 35.91% | 79/220 |
| G06 | 8.16% | 8/98 |
| G07 | 0.00% | 0/74 |
| G08 | 26.42% | 28/106 |
| G09 | 34.12% | 29/85 |
| G10 | 34.13% | 43/126 |
| IN-A | 15.56% | 21/135 |
| IN-B | 6.34% | 9/142 |
| IN-C | 16.24% | 32/197 |
| IN-D | 0.00% | 0/67 |
| IN-E | 48.39% | 90/186 |
| UK-A | 17.74% | 22/124 |
| US-A | 30.30% | 40/132 |

Top error signatures:

1. `unit` × 105
2. `range` × 93
3. `value+unit+range+flag` × 82
4. `value+unit+range` × 73
5. `value+range` × 63
6. `value+flag` × 57
7. `name` × 43
8. `unit+flag` × 38
9. `value+range+flag` × 35
10. `unit+range+flag` × 26
11. `range+flag` × 23
12. `flag` × 20
13. `unit+range` × 17
14. `name+unit` × 13
15. `value` × 11
16. `name+value+unit+range` × 10
17. `name+range+flag` × 8
18. `name+value+unit+range+flag` × 8
19. `value+unit+flag` × 7
20. `name+unit+range+flag` × 4

#### test / rot90

- M4-post 0.00% (0/146); M5-pre 0.00% (0/4); M5-oracle 100.00% (4/4); M5-oracle-clinical 100.00% (4/4); R-NR 0.00% (0/4)
- Recall 0.00% (0/146); precision 0.00% (0/9)
- Invented rows by decoy kind: none=6, phone=2, end_of_report=1
- Omitted rows by GT feature: flag_column=46, flag_first=46, paediatric=28, plain_table=28, sex_labelled_range=2, western_grouping=1
- Headline confusion (GT>confirmed): abnormal>unknown=2, critical>unknown=2
- Pre-review confusion (GT>product): abnormal>unknown=2, critical>unknown=2
- Job status: empty=2, ok=2

| Family | M3 | Count |
|---|---|---|
| G01 | 0.00% | 0/29 |
| G03 | 0.00% | 0/54 |
| G06 | 0.00% | 0/40 |
| IN-E | 0.00% | 0/32 |

## Heldout (aggregate only)

Aggregate metrics only. No per-report, per-family, per-analyte or confusion output exists for this split (plan 6.11).

| Split | Rendering | Metric | Target | Value | Count | 95% CI | Result |
|---|---|---|---|---|---|---|---|
| heldout | native | M1 silent-error rate | <= 0.5% | 26.28% | 527/2005 | 24.40% to 28.26% | FAIL |
| heldout | native | M2 critical recall | = 100% | 64.44% | 29/45 | 49.84% to 76.78% | FAIL |
| heldout | native | M2_post critical handling after confirmation (S3) | = 100% | 60.00% | 27/45 | 45.45% to 72.98% | FAIL |
| heldout | native | M3 row accuracy before review | >= 92% | 37.29% | 852/2285 | 35.33% to 39.29% | FAIL |
| heldout | native | M4 false-critical rate | <= 0.5% | 0.00% | 0/2005 | 0.00% to 0.19% | PASS |
| heldout | native | M5 headline on confirmed | >= 98% | 72.73% | 56/77 | 61.88% to 81.42% | FAIL |
| heldout | native | M6 normal → ready | >= 90% | 0.00% | 0/14 | 0.00% to 21.53% | FAIL |
| heldout | native | M7 review burden | <= 15% | 48.69% | 927/1904 | 46.45% to 50.93% | FAIL |
| heldout | csv | M1 silent-error rate | <= 0.5% | 29.32% | 39/133 | 22.25% to 37.55% | FAIL |
| heldout | csv | M2 critical recall | = 100% | n/a | 0/0 | n/a | n/a |
| heldout | csv | M2_post critical handling after confirmation (S3) | = 100% | n/a | 0/0 | n/a | n/a |
| heldout | csv | M3 row accuracy before review | >= 92% | 40.15% | 55/137 | 32.31% to 48.51% | FAIL |
| heldout | csv | M4 false-critical rate | <= 0.5% | 0.00% | 0/133 | 0.00% to 2.81% | PASS |
| heldout | csv | M5 headline on confirmed | >= 98% | 66.67% | 2/3 | 20.77% to 93.85% | FAIL |
| heldout | csv | M6 normal → ready | >= 90% | 0.00% | 0/2 | 0.00% to 65.76% | FAIL |
| heldout | csv | M7 review burden | <= 15% | 31.85% | 43/135 | 24.59% to 40.12% | FAIL |
| heldout | scan | M1 silent-error rate | <= 1.5% | 35.81% | 718/2005 | 33.74% to 37.93% | FAIL |
| heldout | scan | M2 critical recall | = 100% | 55.56% | 25/45 | 41.18% to 69.06% | FAIL |
| heldout | scan | M2_post critical handling after confirmation (S3) | = 100% | 51.11% | 23/45 | 37.00% to 65.04% | FAIL |
| heldout | scan | M3 row accuracy before review | >= 80% | 29.34% | 625/2130 | 27.45% to 31.31% | FAIL |
| heldout | scan | M4 false-critical rate | <= 0.5% | 0.30% | 6/2005 | 0.14% to 0.65% | PASS |
| heldout | scan | M5 headline on confirmed | >= 98% | 72.73% | 56/77 | 61.88% to 81.42% | FAIL |
| heldout | scan | M6 normal → ready | >= 90% | 0.00% | 0/14 | 0.00% to 21.53% | FAIL |
| heldout | scan | M7 review burden | info | 48.06% | 681/1417 | 45.47% to 50.66% | info |
| heldout | photo | M1 silent-error rate | <= 1.5% | 44.79% | 898/2005 | 42.62% to 46.97% | FAIL |
| heldout | photo | M2 critical recall | = 100% | 35.56% | 16/45 | 23.22% to 50.16% | FAIL |
| heldout | photo | M2_post critical handling after confirmation (S3) | = 100% | 33.33% | 15/45 | 21.36% to 47.93% | FAIL |
| heldout | photo | M3 row accuracy before review | >= 70% | 16.45% | 372/2262 | 14.98% to 18.03% | FAIL |
| heldout | photo | M4 false-critical rate | <= 0.5% | 1.05% | 21/2005 | 0.69% to 1.60% | FAIL |
| heldout | photo | M5 headline on confirmed | >= 98% | 57.14% | 76/133 | 48.65% to 65.24% | FAIL |
| heldout | photo | M6 normal → ready | >= 90% | 0.00% | 0/42 | 0.00% to 8.38% | FAIL |
| heldout | photo | M7 review burden | info | 59.39% | 775/1305 | 56.70% to 62.02% | info |
| heldout | rot90 | M1 silent-error rate | <= 1.5% | 21.05% | 8/38 | 11.07% to 36.35% | FAIL |
| heldout | rot90 | M2 critical recall | = 100% | n/a | 0/0 | n/a | n/a |
| heldout | rot90 | M2_post critical handling after confirmation (S3) | = 100% | n/a | 0/0 | n/a | n/a |
| heldout | rot90 | M3 row accuracy before review | >= 80% | 0.00% | 0/39 | 0.00% to 8.97% | FAIL |
| heldout | rot90 | M4 false-critical rate | <= 0.5% | 0.00% | 0/38 | 0.00% to 9.18% | PASS |
| heldout | rot90 | M5 headline on confirmed | >= 98% | 0.00% | 0/4 | 0.00% to 48.99% | FAIL |
| heldout | rot90 | M6 normal → ready | >= 90% | 0.00% | 0/2 | 0.00% to 65.76% | FAIL |
| heldout | rot90 | M7 review burden | info | 100.00% | 1/1 | 20.65% to 100.00% | info |

| Rendering | Jobs | Legibility | M1 wrong | M1 invented | M1 omitted | Crashes by class |
|---|---|---|---|---|---|---|
| native | 77 | 100.00% | 144 | 15 | 368 | none |
| csv | 3 | 98.50% | 37 | 0 | 2 | none |
| scan | 77 | 93.62% | 114 | 16 | 588 | none |
| photo | 133 | 92.72% | 149 | 17 | 732 | none |
| rot90 | 4 | 23.68% | 0 | 0 | 8 | none |

## Gate

- Mode `none`: PASS

## Known v0 limitations

- Per-token OCR confidence, column-parser origin, the plausibility check and unread lines as rows are not surfaced by the current product, so v0 never flags on them and never covers an omission (plan 6.3.3).
- Qualitative and titre rows always carry no analyte key in the product; the name comparator runs in key-optional mode for them (plan 6.7).
- The v0 scorer does not model the ensemble, which can raise a no-anomaly report to abnormal; the effect is visible in M5-pre. P2 replaces this scorer with the real confirmed-rows scoring (plan 6.4).
- Legacy urine rows are never flagged for review: the product shows no uncertainty signal for them.
- The evaluation measures the offline, no-LLM configuration of the product.

## Runtime (non-deterministic)

- Wall time: 1857.08 s; OCR cache hits: 2010, misses: 0
- native: n=380, p50 6.082695021000006 s, p95 10.984091872000135 s
- csv: n=20, p50 0.10826831800000036 s, p95 0.28122928900000943 s
- scan: n=380, p50 8.711832957999832 s, p95 15.715983612999935 s
- photo: n=660, p50 2.059943742999849 s, p95 2.788099561000081 s
- rot90: n=19, p50 6.245042001000002 s, p95 13.657260096999948 s

