# LATRICS Operations Suite

One desktop window that brings every LATRICS survey-data tool together: pre-flight data checks, photo processing and LiDAR processing. Pick a tool in the left sidebar; the Dashboard shows what each tool does and which Python packages it needs.

Built by the Operations team at **Latrics India Pvt Ltd (LIPL)** for UAV photogrammetry and LiDAR survey work.

---

## Tools

| Group | Tool | What it does | Output |
|---|---|---|---|
| **Checks** | **GNSS Pre-Check** | Scores base and rover (drone) RINEX files satellite by satellite, checks take-off / landing coverage and compares base vs rover before PPK. | Word report + CSV |
| | **Data Pre-Check** | Three stages in one window: before event generation (disk, image quality), after event generation (events vs images, solution colours) and after geotagging (GCP check). | Word report + CSV |
| **Photo processing** | **Image Enhancer** | Finds dark, over-exposed and blurry photos, improves them and writes a **new** folder. Originals are never changed. Includes an OLD vs NEW compare view and a coverage check for weak images. | `<flight>\Enhanced` + `Enhancement_Report.csv` |
| | **Event Generation** | Batch run for many flights: decodes the KQ `.dat`, runs RTKLIB `convbin` + `rnx2rtkp` (PPK), interpolates the trajectory at every camera event and matches the photos. | `Pos_Camera.txt` + `Event_Report.csv` |
| | **Geotag Pipeline** | Rover SBF and base `.T04` to RINEX, PPK, event matching and geotagging, for one flight or a whole date folder. | Matched CSV, Excel, geotagged JPG copies |
| | **Ortho Launcher** | Scans flights and event files, sets Metashape server and match settings, then submits the network ortho job. | Metashape project / ortho |
| **LiDAR processing** | **LiDAR Tool** | POS trajectory, automatic strip division with preview, PointsCreator `.lip` + `stripinfo.json`, and Start Calc for one flight or a queue. | `.lip`, `stripinfo.json`, PointsCreator results |

Every tool is embedded unchanged inside `latrics_suite.py`; the Suite only adds the window, sidebar and dashboard.

---

## Requirements

- Windows (the field workflow is Windows-based; the LiDAR Tool uses `pywinauto` to drive PointsCreator)
- Python 3.8 or newer, with `tkinter` (included in the standard Windows installer)
- Python packages, depending on the tools you use:

| Package | Used by |
|---|---|
| `numpy` | all tools except Ortho Launcher |
| `pillow` | Data Pre-Check, Image Enhancer, Geotag Pipeline |
| `matplotlib` | GNSS Pre-Check, Data Pre-Check |
| `python-docx` | GNSS Pre-Check, Data Pre-Check (Word reports) |
| `piexif`, `openpyxl` | Geotag Pipeline |
| `pywinauto` | LiDAR Tool |
| `pyproj` (optional) | Event Generation, LiDAR Tool |

You do not need to install them all up front. The **Dashboard** lists the missing packages and can install them for you. To install them by hand:

```
pip install numpy pillow matplotlib python-docx piexif openpyxl pywinauto pyproj
```

### External software (only for some tools)

- **RTKLIB** (`convbin.exe`, `rnx2rtkp.exe`): needed by Event Generation and Geotag Pipeline. Put them in a `bin` folder next to the Suite.
- **Agisoft Metashape** network server: needed by Ortho Launcher.
- **PointsCreator**: needed by the LiDAR Tool.

---

## Run

```
python latrics_suite.py
```

The Dashboard opens. To open straight into one tool:

```
python latrics_suite.py --page enhancer
```

Available pages: `gnss`, `data`, `enhancer`, `events`, `geotag`, `ortho`, `lidar`.

Tool settings are saved in small settings files next to `latrics_suite.py`.

---

## Image Enhancer in brief

1. **Add flights**: add one flight folder, or a main folder with all flights inside; tick the ones to process.
2. **Scan**: each image is checked for blur, darkness and over-exposure (same limits as Data Pre-Check; you can change them).
3. **Enhance**: ticked images are corrected. Each flight's results go into its own `Enhanced` folder, with the good images copied across. The original images are never touched.
4. **Compare**: double-click a corrected image to see OLD vs NEW side by side.
5. **Read the advice**: images that cannot be rescued are marked "re-shoot advised". The **Coverage advice** column then says, per weak image, whether it is **Safe to remove** (neighbouring photos still overlap enough) or the strip needs a **RE-FLY**. This uses the GPS in the images and the forward overlap you set (default 80% flown, 60% minimum).

Limits to know about: heavily blurred or burnt-out (pure white) photos cannot be fully restored. The coverage check looks along the flight line only, not at side overlap between strips.

---

## Repository contents

```
latrics_suite.py     the whole Suite (single file, all tools embedded)
README.md            this file
```

## Support

Operations team, Latrics India Pvt Ltd: `<priyanshujatan30@gmail.com>`
