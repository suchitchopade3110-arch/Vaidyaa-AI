# Test fixtures

- `nih_cxr_00000001_000.png` — a single de-identified frontal chest radiograph
  from the NIH Clinical Center ChestX-ray14 dataset (image `00000001_000.png`,
  dataset label: Cardiomegaly), as redistributed in the torchxrayvision test
  suite (https://github.com/mlmed/torchxrayvision/tree/master/tests). Used to
  check that the chest X-ray classifier sees the whole radiograph and that the
  radiograph plausibility gate accepts a real film.
  Source dataset: Wang et al., "ChestX-ray8", CVPR 2017;
  https://nihcc.app.box.com/v/ChestXray-NIHCC
- `non_radiographs/*.png` — synthetic images (a text page, random shapes,
  blurred blobs, noise) generated for this test suite. The radiograph
  plausibility gate must reject every one of them.
