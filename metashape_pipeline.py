#!/usr/bin/env python3
"""
LATRICS Ortho Pipeline  (Metashape side)

Started by ortho_launcher.py (GUI) or folder_daemon.py:
    metashape.exe -r metashape_pipeline.py  <job.json>

  * <job.json>  = written by the launcher (flights, server, match settings ...)
  * no argument = uses ortho_settings.json next to this script (last settings saved in the launcher)
                  and scans the main folder itself  -> folder_daemon.py keeps working unchanged

Supported folder layouts inside the MAIN folder (scanned automatically, any depth):
  A) PPK Event Tool layout   <Main>\\<Flight>\\Image\\*.JPG   + <Flight>\\Pos_Camera*.txt
  B) Old layout              <Main>\\Image\\<FlightN>\\*.JPG  + <Main>\\Events\\**\\*N*.txt
  GCP  : <Main>\\GCP\\*.txt|csv            (Name X Y Z)
  DEM  : <Main>\\<...DEM... or Lidar...>\\*.tif

This file can also be imported WITHOUT Metashape (the launcher uses its folder scanner).
"""

import os
import re
import sys
import json
import glob
import datetime
import traceback

try:
    import Metashape
except ImportError:                      # imported by the launcher (normal Python)
    Metashape = None

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(SCRIPT_DIR, "ortho_settings.json")
IMG_EXTS = ('.jpg', '.jpeg', '.tif', '.tiff')
SKIP_DIRS = {'bin', 'base', 'pos', 'gcp', 'events', 'event', 'exports', 'lidar', 'lidar dem', 'dem',
             'resultout', '__pycache__', 'jobs', 'logs'}
SKIP_PREFIX = ('ppk_output', 'ortho_output')
DONE_MARK = "[PIPELINE DONE]"
FAIL_MARK = "[PIPELINE FAILED]"

DEFAULTS = {
    'main_folder': r"//LAT-22-002/2026-08-25_Flight_Orthomosaic",
    'server_ip': "192.168.1.54",
    'server_port': 5840,
    'epsg': 32644,
    'min_altitude': 100.0,               # m
    'altitude_mode': 'absolute',         # 'absolute' = event height >= value ; 'above_lowest' = lowest photo + value
    'edge_photos': 2,                    # photos kept before / after the survey grid
    'match_downscale': 1,                # 0 Highest, 1 High, 2 Medium, 4 Low, 8 Lowest
    'keypoint_limit': 40000,
    'tiepoint_limit': 4000,
    'generic_preselection': True,
    'reference_preselection': True,
    'import_opk': True,                  # Pos_Camera.txt has Omega/Phi/Kappa columns
    'gcp_path': '',
    'dem_path': '',
    'build_ortho': False,
    'ortho_surface': 'photos',           # 'photos' = DEM from depth maps ; 'lidar_dem' = use dem_path
    'depth_downscale': 4,                # 1 Ultra, 2 High, 4 Medium, 8 Low, 16 Lowest
    'ortho_resolution': 0.0,             # m, 0 = auto
    'export_ortho': True,
    'output_dir': '',                    # default <Main>\Ortho_Output
    'project_name': 'Merged_Project',
    'flights': [],                       # [{'name', 'photo_dir', 'events': [...]}]  empty = scan main folder
    'log_file': '',
    'quit_when_done': False,
}


# =====================================================================
# FOLDER SCAN  (no Metashape needed)
# =====================================================================

def natkey(s):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r'(\d+)', s)]


def list_images(d):
    try:
        return sorted([f for f in os.listdir(d) if f.lower().endswith(IMG_EXTS)
                       and os.path.isfile(os.path.join(d, f))], key=str.lower)
    except OSError:
        return []


def _subdir(folder, names):
    try:
        for d in os.listdir(folder):
            if d.lower() in names and os.path.isdir(os.path.join(folder, d)):
                return os.path.join(folder, d)
    except OSError:
        pass
    return None


def _skip(name):
    n = name.lower()
    return n in SKIP_DIRS or n.startswith(SKIP_PREFIX)


def flight_label(photo_dir, main):
    d = photo_dir
    if os.path.basename(d).lower() in ('image', 'images') and os.path.abspath(d) != os.path.abspath(main):
        d = os.path.dirname(d)
    rel = os.path.relpath(d, main)
    return os.path.basename(os.path.abspath(main)) if rel == '.' else rel


def find_event_files(photo_dir, main):
    """1) Pos_Camera*.txt from the PPK Event Tool (photo folder or its parent)
       2) <Main>\\Events\\**\\*.txt whose name has the same flight number (Flight3 -> *3*, not *13*)"""
    parent = os.path.dirname(photo_dir)
    for d in (photo_dir, parent):
        c = sorted(glob.glob(os.path.join(d, 'Pos_Camera*.txt')), key=natkey)
        if c:
            return c
    ev, d, top = None, parent, os.path.abspath(main)       # nearest Events folder, up to the main folder
    while ev is None:
        ev = _subdir(d, {'events', 'event'})
        if os.path.abspath(d) == top or os.path.dirname(d) == d:
            break
        d = os.path.dirname(d)
    if not ev:
        return []
    name = os.path.basename(photo_dir)
    if name.lower() in ('image', 'images'):
        name = os.path.basename(parent)
    nums = re.findall(r'\d+', name)
    if not nums:
        return []
    pat = re.compile(r'(?<!\d)0*' + str(int(nums[-1])) + r'(?!\d)')
    files = sorted(glob.glob(os.path.join(ev, '**', '*.txt'), recursive=True), key=natkey)
    hits = [f for f in files if pat.search(os.path.splitext(os.path.basename(f))[0])]
    return hits[:1]


def event_names(path):
    names = set()
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                t = re.split(r'[\s,;]+', line.strip())
                if t and t[0] and not t[0].startswith('#'):
                    names.add(os.path.splitext(t[0])[0].lower())
    except OSError:
        pass
    return names


def count_matched(photo_dir, events):
    names = set()
    for e in events:
        names |= event_names(e)
    return sum(1 for f in list_images(photo_dir) if os.path.splitext(f)[0].lower() in names)


def discover_flights(main, max_depth=5):
    """every folder that directly holds photos = one flight"""
    main = os.path.abspath(main)
    flights = []

    def visit(d, depth):
        imgs = list_images(d)
        if imgs:
            ev = find_event_files(d, main)
            flights.append({'name': flight_label(d, main), 'photo_dir': d, 'photos': len(imgs),
                            'events': ev, 'matched': count_matched(d, ev) if ev else 0})
            return
        if depth >= max_depth:
            return
        try:
            subs = sorted(os.listdir(d), key=natkey)
        except OSError:
            return
        for s in subs:
            p = os.path.join(d, s)
            if os.path.isdir(p) and not _skip(s):
                visit(p, depth + 1)
    visit(main, 0)
    return flights


def find_gcp(main):
    g = _subdir(main, {'gcp', 'gcps'})
    if g:
        for pat in ('*.txt', '*.csv'):
            f = sorted(glob.glob(os.path.join(g, pat)))
            if f:
                return f[0]
    return ''


def find_dem(main):
    try:
        for d in sorted(os.listdir(main)):
            p = os.path.join(main, d)
            if os.path.isdir(p) and ('dem' in d.lower() or 'lidar' in d.lower()):
                f = sorted(glob.glob(os.path.join(p, '*.tif')) + glob.glob(os.path.join(p, '*.tiff')))
                if f:
                    return f[0]
    except OSError:
        pass
    return ''


def detect_epsg(main):
    """EPSG from the newest Batch_Summary_*.csv written by the PPK Event Tool"""
    files = sorted(glob.glob(os.path.join(main, 'Batch_Summary_*.csv')))
    if not files:
        return None
    try:
        with open(files[-1], 'r', encoding='utf-8', errors='ignore') as f:
            head = f.readline().strip().split(',')
            if 'EPSG' not in head:
                return None
            k = head.index('EPSG')
            for line in f:
                v = line.split(',')
                if len(v) > k and v[k].strip().isdigit():
                    return int(v[k])
    except OSError:
        pass
    return None


# =====================================================================
# METASHAPE PIPELINE
# =====================================================================

def make_logger(path):
    fh = None
    if path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fh = open(path, 'a', encoding='utf-8')

    def log(msg):
        print(msg)
        if fh:
            fh.write(msg + "\n")
            fh.flush()
    return log


def set_attrs(task, log, **kw):
    """set task options, skipping ones this Metashape version doesn't have"""
    for k, v in kw.items():
        try:
            setattr(task, k, v)
        except (AttributeError, TypeError) as ex:
            log(f"    (note: {type(task).__name__}.{k} skipped - {ex})")
    return task


def _delimiter(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                if line.strip() and not line.startswith('#'):
                    return ',' if ',' in line else ' \t'
    except OSError:
        pass
    return ' \t'


def import_reference(chunk, path, crs, columns, log, markers=False):
    kw = dict(path=path, format=Metashape.ReferenceFormatCSV, crs=crs, columns=columns,
              delimiter=_delimiter(path), skip_rows=0)
    if _delimiter(path) != ',':
        kw['group_delimiters'] = True
    if markers:
        kw['create_markers'] = True
    try:
        chunk.importReference(**kw)
    except TypeError:                              # older API without some keywords
        kw.pop('create_markers', None)
        kw.pop('group_delimiters', None)
        chunk.importReference(**kw)


def filter_cameras(chunk, cfg, log):
    """remove takeoff / landing / transit photos (keeps edge_photos around the survey grid)"""
    zs = [c.reference.location.z for c in chunk.cameras if c.reference.location]
    if not zs:
        log("    no reference heights - altitude filter skipped")
        return
    thr = float(cfg['min_altitude'])
    if cfg.get('altitude_mode') == 'above_lowest':
        thr = min(zs) + thr
        log(f"    altitude threshold {thr:.1f} m (lowest photo {min(zs):.1f} m + {cfg['min_altitude']} m)")
    start = end = None
    for i, c in enumerate(chunk.cameras):
        if c.reference.location and c.reference.location.z >= thr:
            if start is None:
                start = i
            end = i
    if start is None:
        log(f"    WARNING: no photo above {thr:.1f} m - nothing removed, check altitude setting")
        return
    pad = int(cfg.get('edge_photos', 2))
    start, end = max(0, start - pad), min(len(chunk.cameras) - 1, end + pad)
    rm = [c for i, c in enumerate(chunk.cameras)
          if not c.reference.location or i < start or i > end or c.reference.location.z < thr]
    if rm:
        log(f"    removing {len(rm)} takeoff/landing/transit photos ({len(chunk.cameras) - len(rm)} kept)")
        chunk.remove(rm)


def merge_chunks(doc, chunks, log):
    if len(chunks) == 1:
        return chunks[0]
    n = len(doc.chunks)
    for args in ({'chunks': [c.key for c in chunks], 'merge_markers': True},
                 {'chunks': [c.key for c in chunks]},
                 {'chunks': chunks}):
        try:
            doc.mergeChunks(**args)
            break
        except Exception as ex:
            last = ex
    else:
        raise RuntimeError(f"mergeChunks failed: {last}")
    if len(doc.chunks) == n:
        raise RuntimeError("mergeChunks produced no chunk")
    m = doc.chunks[-1]
    m.label = "Merged Chunk"
    return m


def tiff_compression(log):
    """BigTIFF (>4 GB) + tiled + LZW + overviews - a plain TIFF fails with 'TIFFWriteTile' on large orthos"""
    try:
        c = Metashape.ImageCompression()
        c.tiff_big = True
        c.tiff_tiled = True
        c.tiff_overviews = True
        c.tiff_compression = Metashape.ImageCompression.TiffCompressionLZW
        return c
    except Exception as ex:
        log(f"    (note: TIFF compression options not available - {ex})")
        return None


def run_pipeline(cfg, log):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    main = cfg['main_folder']
    out_dir = cfg.get('output_dir') or os.path.join(main, 'Ortho_Output')
    exports = os.path.join(out_dir, 'Exports')
    os.makedirs(exports, exist_ok=True)
    project = os.path.join(out_dir, f"{cfg.get('project_name') or 'Merged_Project'}_{ts}.psx")
    crs = Metashape.CoordinateSystem(f"EPSG::{int(cfg['epsg'])}")

    log("=== LATRICS METASHAPE PIPELINE ===")
    log(f"Main folder : {main}")
    log(f"Project     : {project}")
    log(f"CRS         : EPSG {cfg['epsg']}")

    flights = cfg.get('flights') or discover_flights(main)
    if not flights:
        raise RuntimeError(f"No photo folders found in {main}")
    log(f"Flights     : {len(flights)}")

    # 1. network server
    client = Metashape.NetworkClient()
    client.connect(cfg['server_ip'], int(cfg['server_port']))
    log(f"Connected to network server {cfg['server_ip']}:{cfg['server_port']}")

    doc = Metashape.Document()
    doc.save(project)
    chunks = []
    cols = "nxyzabc" if cfg.get('import_opk', True) else "nxyz"

    # 2. one chunk per flight
    for k, fl in enumerate(flights, 1):
        pdir = fl['photo_dir']
        log(f"\n[{k}/{len(flights)}] {fl.get('name') or os.path.basename(pdir)}")
        photos = [os.path.join(pdir, f) for f in list_images(pdir)]
        if not photos:
            log("    WARNING: no photos - skipped")
            continue
        chunk = doc.addChunk()
        chunk.label = fl.get('name') or os.path.basename(pdir)
        chunk.crs = crs
        if cfg.get('import_opk', True):
            try:
                chunk.euler_angles = Metashape.EulerAnglesOPK      # Pos_Camera.txt = Omega Phi Kappa
            except AttributeError:
                pass
        chunk.addPhotos(photos)
        log(f"    {len(chunk.cameras)} photos added")

        events = [e for e in (fl.get('events') or []) if os.path.isfile(e)]
        for ev in events:
            log(f"    event file: {ev}")
            import_reference(chunk, ev, crs, cols, log)
        if not events:
            log("    WARNING: no event file - photos have no reference positions")
        filter_cameras(chunk, cfg, log)
        if not chunk.cameras:
            log("    WARNING: chunk empty after filtering - removed")
            try:
                doc.remove([chunk])
            except Exception:
                pass
            continue
        chunks.append(chunk)

    if not chunks:
        raise RuntimeError("No usable flight chunks")

    # 3. merge + GCP + DEM
    log(f"\n[MERGE] {len(chunks)} chunk(s) ...")
    merged = merge_chunks(doc, chunks, log)
    log(f"    merged chunk: {len(merged.cameras)} photos")

    gcp = cfg.get('gcp_path')
    if gcp and os.path.isfile(gcp):
        log(f"[GCP] {gcp}")
        import_reference(merged, gcp, crs, "nxyz", log, markers=True)

    surface = cfg.get('ortho_surface', 'photos')
    if cfg.get('build_ortho') and surface == 'lidar_dem':
        dem = cfg.get('dem_path')
        try:
            if not dem or not os.path.isfile(dem):
                raise FileNotFoundError(f"DEM not found: {dem}")
            merged.importRaster(path=dem, crs=crs, raster_type=Metashape.ElevationData)
            log(f"[DEM] imported LiDAR DEM {dem}")
        except Exception as ex:
            log(f"[DEM] WARNING: LiDAR DEM import failed ({ex}) - DEM will be built from photos")
            surface = 'photos'
    doc.save()

    # 4. network tasks
    log("\n[SUBMIT] encoding network tasks ...")
    T = Metashape.Tasks
    tasks = []

    t = set_attrs(T.MatchPhotos(), log, downscale=int(cfg['match_downscale']),
                  keypoint_limit=int(cfg['keypoint_limit']), tiepoint_limit=int(cfg['tiepoint_limit']),
                  generic_preselection=bool(cfg['generic_preselection']),
                  reference_preselection=bool(cfg['reference_preselection']))
    tasks.append(t.toNetworkTask(merged))
    tasks.append(T.AlignCameras().toNetworkTask(merged))
    tasks.append(T.OptimizeCameras().toNetworkTask(merged))
    names = ["MatchPhotos", "AlignCameras", "OptimizeCameras"]

    if cfg.get('build_ortho'):
        if surface == 'photos':
            tasks.append(set_attrs(T.BuildDepthMaps(), log, downscale=int(cfg['depth_downscale']),
                                   filter_mode=Metashape.MildFiltering).toNetworkTask(merged))
            pc = getattr(T, 'BuildPointCloud', None) or getattr(T, 'BuildDenseCloud')
            tasks.append(pc().toNetworkTask(merged))
            src = getattr(Metashape, 'PointCloudData', None) or getattr(Metashape, 'DenseCloudData')
            tasks.append(set_attrs(T.BuildDem(), log, source_data=src,
                                   interpolation=Metashape.EnabledInterpolation).toNetworkTask(merged))
            names += ["BuildDepthMaps", pc.__name__, "BuildDem"]
        o = set_attrs(T.BuildOrthomosaic(), log, surface_data=Metashape.ElevationData)
        if float(cfg.get('ortho_resolution') or 0) > 0:
            set_attrs(o, log, resolution=float(cfg['ortho_resolution']))
        tasks.append(o.toNetworkTask(merged))
        names.append("BuildOrthomosaic")
        if cfg.get('export_ortho', True):
            tif = os.path.join(exports, f"{cfg.get('project_name') or 'Merged_Project'}_{ts}_Ortho.tif")
            e = set_attrs(T.ExportRaster(), log, path=tif, source_data=Metashape.OrthomosaicData,
                          image_format=Metashape.ImageFormatTIFF, save_world=True)
            comp = tiff_compression(log)
            if comp is not None:
                set_attrs(e, log, image_compression=comp)
            tasks.append(e.toNetworkTask(merged))
            names.append("ExportRaster")
            log(f"    ortho export -> {tif}")
    log("    tasks: " + " -> ".join(names))

    batch_id = client.createBatch(project, tasks)
    client.setBatchPaused(batch_id, False)
    log(f"\n=== BATCH SUBMITTED (Batch ID: {batch_id}) ===")
    with open(os.path.join(out_dir, f"Batch_{ts}.json"), 'w') as f:
        json.dump({'batch_id': batch_id, 'project': project, 'tasks': names,
                   'flights': [c.label for c in chunks], 'settings': {k: v for k, v in cfg.items() if k != 'flights'}},
                  f, indent=2, default=str)
    return batch_id


def load_config(argv):
    cfg = dict(DEFAULTS)
    path = next((a for a in argv[1:] if a.lower().endswith('.json') and os.path.isfile(a)), None)
    if not path and os.path.isfile(SETTINGS_FILE):
        path = SETTINGS_FILE
    if path:
        with open(path, 'r', encoding='utf-8') as f:
            cfg.update(json.load(f))
    if path == SETTINGS_FILE:
        cfg['flights'] = []                          # daemon run: always rescan
    return cfg, path


if __name__ == "__main__":
    if Metashape is None:
        print("Run this inside Metashape:  metashape.exe -r metashape_pipeline.py [job.json]")
        sys.exit(1)
    cfg, src = load_config(sys.argv)
    log = make_logger(cfg.get('log_file'))
    log(f"Settings from: {src or 'built-in defaults'}")
    try:
        run_pipeline(cfg, log)
        log(DONE_MARK)
    except Exception as ex:
        log(traceback.format_exc())
        log(f"{FAIL_MARK} {ex}")
    finally:
        if cfg.get('quit_when_done'):
            try:
                Metashape.app.quit()
            except Exception:
                pass