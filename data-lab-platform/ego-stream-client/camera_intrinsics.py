"""OAK EEPROM camera intrinsics export (depthai_eeprom_v1 schema).

Reading follows Luxonis Calibration Reader:
https://docs.oakchina.cn/projects/api/samples/calibration/calibration_reader.html
  - device.readCalibration()
  - calib.getDefaultIntrinsics(socket)
  - calib.getCameraIntrinsics(socket, width, height)
  - calib.getDistortionCoefficients(socket)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

INTRINSICS_SCHEMA = "depthai_eeprom_v1"
INTRINSICS_REL_PATH = Path("meta") / "camera_intrinsics.json"
INTRINSICS_STATUS_VALID = "valid"
INTRINSICS_STATUS_INVALID = "INTRINSICS_INVALID"
COEFF_EPS = 1e-12

# DepthAI CameraBoardSocket numeric ids (CAM_A..CAM_D).
OAK_SOCKET_TO_CAMERA_ID: dict[str, int] = {
    "CAM_A": 0,
    "CAM_B": 1,
    "CAM_C": 2,
    "CAM_D": 3,
}


def read_calibration_from_device(device: Any) -> tuple[Any, str]:
    """
    Load CalibrationHandler using the official reader order.

    Prefer ``readCalibration()`` (Calibration Reader sample), then ``readCalibration2()``,
    then ``readFactoryCalibration()`` when user EEPROM has no cameraData.
    """
    candidates: list[tuple[str, Any]] = []

    for source, reader in (
        ("readCalibration", device.readCalibration),
        ("readCalibration2", device.readCalibration2),
    ):
        try:
            calib = reader()
            candidates.append((source, calib))
            if (_eeprom_camera_data_count(calib) or 0) > 0:
                return calib, source
        except Exception:
            continue

    try:
        factory = device.readFactoryCalibration()
        if (_eeprom_camera_data_count(factory) or 0) > 0:
            return factory, "readFactoryCalibration"
        candidates.append(("readFactoryCalibration", factory))
    except Exception:
        pass

    for preferred in ("readCalibration", "readCalibration2", "readFactoryCalibration"):
        for source, calib in candidates:
            if source == preferred:
                return calib, source

    if candidates:
        return candidates[0][1], candidates[0][0]
    raise RuntimeError("unable to read calibration from OAK device")


def _distortion_model_name(calib: Any, socket: Any) -> str:
    try:
        model = calib.getDistortionModel(socket)
        name = getattr(model, "name", str(model)).lower()
        if "fisheye" in name:
            return "fisheye"
        if "perspective" in name:
            return "perspective"
        return name
    except Exception:
        return "unknown"


def _matrix_to_intrinsics(mat: np.ndarray) -> tuple[float, float, float, float]:
    return float(mat[0, 0]), float(mat[1, 1]), float(mat[0, 2]), float(mat[1, 2])


def _scale_intrinsics_matrix(mat: np.ndarray, from_w: int, from_h: int, to_w: int, to_h: int) -> np.ndarray:
    out = mat.copy()
    sx = float(to_w) / float(from_w)
    sy = float(to_h) / float(from_h)
    out[0, 0] *= sx
    out[1, 1] *= sy
    out[0, 2] *= sx
    out[1, 2] *= sy
    return out


def _normalize_distortion_coeffs(raw: list[Any]) -> tuple[list[float], list[float]]:
    """Return (fisheye_4, full_list) for cv2.fisheye and audit."""
    full = [float(x) for x in raw]
    fisheye = full[:4]
    while len(fisheye) < 4:
        fisheye.append(0.0)
    return fisheye, full


def _read_distortion_coefficients(calib: Any, socket: Any) -> tuple[list[float], list[float], bool, str]:
    """Official Calibration Reader: getDistortionCoefficients(socket)."""
    try:
        raw = list(calib.getDistortionCoefficients(socket))
        if raw:
            fisheye, full = _normalize_distortion_coeffs(raw)
            return fisheye, full, True, "getDistortionCoefficients"
    except Exception:
        pass
    return [0.0, 0.0, 0.0, 0.0], [], False, "missing"


def _read_intrinsics_matrix(
    calib: Any,
    socket: Any,
    width: int,
    height: int,
) -> tuple[np.ndarray | None, str, bool]:
    """
  Official order:
  1. getCameraIntrinsics(socket, width, height) at ISP output resolution
  2. getDefaultIntrinsics(socket) scaled to target resolution
    """
    w, h = int(width), int(height)
    try:
        mat = np.array(calib.getCameraIntrinsics(socket, w, h), dtype=np.float64)
        if mat.shape == (3, 3):
            return mat, "getCameraIntrinsics", True
    except Exception:
        pass

    try:
        mat_default, def_w, def_h = calib.getDefaultIntrinsics(socket)
        mat = np.array(mat_default, dtype=np.float64)
        def_w_i, def_h_i = int(def_w), int(def_h)
        if def_w_i > 0 and def_h_i > 0 and (def_w_i != w or def_h_i != h):
            mat = _scale_intrinsics_matrix(mat, def_w_i, def_h_i, w, h)
        if mat.shape == (3, 3):
            return mat, "getDefaultIntrinsics", True
    except Exception:
        pass

    return None, "fallback", False


def _read_from_eeprom_camera_data(
    calib: Any,
    *,
    oak_socket: str | None,
    width: int,
    height: int,
) -> dict[str, Any] | None:
    """Parse calib.eepromToJson()['cameraData'] when handler getters are unavailable."""
    cam_id = OAK_SOCKET_TO_CAMERA_ID.get(oak_socket or "")
    try:
        eeprom = calib.eepromToJson()
    except Exception:
        return None
    if not isinstance(eeprom, dict):
        return None

    camera_data = eeprom.get("cameraData") or []
    for cam in camera_data:
        if not isinstance(cam, dict):
            continue
        cid = cam.get("cameraId")
        if cam_id is not None and cid is not None and int(cid) != int(cam_id):
            continue

        intrinsic = cam.get("intrinsicMatrix") or cam.get("intrinsics")
        dist = cam.get("distortionCoeff") or cam.get("distortionCoefficients") or []
        if not intrinsic:
            continue

        mat = np.array(intrinsic, dtype=np.float64)
        eeprom_w = int(cam.get("width") or width)
        eeprom_h = int(cam.get("height") or height)
        if eeprom_w > 0 and eeprom_h > 0 and (eeprom_w != width or eeprom_h != height):
            mat = _scale_intrinsics_matrix(mat, eeprom_w, eeprom_h, width, height)

        fisheye, full = _normalize_distortion_coeffs(list(dist) if dist else [])
        camera_type = cam.get("cameraType")
        model = "fisheye" if camera_type in (1, "fisheye", "FISHEYE") else "perspective"
        if not full:
            model = "unknown"

        fx, fy, ppx, ppy = _matrix_to_intrinsics(mat)
        return {
            "width": int(width),
            "height": int(height),
            "fx": fx,
            "fy": fy,
            "ppx": ppx,
            "ppy": ppy,
            "distortion_model": model,
            "coeffs": fisheye,
            "distortion_coefficients_full": full,
            "intrinsics_source": "eepromToJson.cameraData",
            "distortion_source": "eepromToJson.cameraData",
            "eeprom_intrinsics_read_ok": True,
            "eeprom_distortion_read_ok": bool(full),
        }
    return None


def entry_has_nonzero_distortion(entry: dict[str, Any]) -> bool:
    coeffs = entry.get("coeffs") or []
    return any(abs(float(c)) > COEFF_EPS for c in coeffs)


def entry_uses_fallback_pinhole(entry: dict[str, Any]) -> bool:
    """True when intrinsics getters failed (max(w,h) focal + image center principal point)."""
    w = int(entry.get("width") or 0)
    h = int(entry.get("height") or 0)
    if w <= 0 or h <= 0:
        return True
    if entry.get("intrinsics_source") == "fallback":
        return True
    fx = float(entry.get("fx") or 0.0)
    fy = float(entry.get("fy") or 0.0)
    ppx = float(entry.get("ppx") or 0.0)
    ppy = float(entry.get("ppy") or 0.0)
    fallback_f = float(max(w, h))
    return (
        abs(fx - fallback_f) <= COEFF_EPS
        and abs(fy - fallback_f) <= COEFF_EPS
        and abs(ppx - w / 2.0) <= COEFF_EPS
        and abs(ppy - h / 2.0) <= COEFF_EPS
    )


def collect_intrinsics_invalid_reasons(document: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    cameras = document.get("cameras") or {}
    if not isinstance(cameras, dict) or not cameras:
        return ["missing_camera_entries"]

    for lerobot_key, entry in cameras.items():
        if not isinstance(entry, dict):
            reasons.append(f"invalid_camera_entry:{lerobot_key}")
            continue
        if not entry_has_nonzero_distortion(entry):
            reasons.append(f"zero_distortion_coeffs:{lerobot_key}")
        if entry_uses_fallback_pinhole(entry):
            reasons.append(f"fallback_pinhole_intrinsics:{lerobot_key}")
        model = str(entry.get("distortion_model") or "unknown").lower()
        if model == "unknown" and not entry_has_nonzero_distortion(entry):
            reasons.append(f"distortion_model_unknown:{lerobot_key}")

    eeprom_camera_count = document.get("eeprom_camera_data_count")
    if eeprom_camera_count == 0:
        reasons.insert(0, "eeprom_camera_data_empty")

    seen: set[str] = set()
    out: list[str] = []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            out.append(reason)
    return out


def finalize_intrinsics_document(document: dict[str, Any]) -> dict[str, Any]:
    """Annotate document with status + per-camera valid flags (v2 failed marking)."""
    doc = dict(document)
    cameras = doc.get("cameras")
    if isinstance(cameras, dict):
        annotated: dict[str, Any] = {}
        for key, entry in cameras.items():
            if not isinstance(entry, dict):
                annotated[key] = entry
                continue
            cam = dict(entry)
            cam_reasons: list[str] = []
            if not entry_has_nonzero_distortion(cam):
                cam_reasons.append("zero_distortion_coeffs")
            if entry_uses_fallback_pinhole(cam):
                cam_reasons.append("fallback_pinhole_intrinsics")
            if str(cam.get("distortion_model") or "unknown").lower() == "unknown":
                if not entry_has_nonzero_distortion(cam):
                    cam_reasons.append("distortion_model_unknown")
            cam["valid"] = not cam_reasons
            if cam_reasons:
                cam["invalid_reasons"] = cam_reasons
            annotated[key] = cam
        doc["cameras"] = annotated

    reasons = collect_intrinsics_invalid_reasons(doc)
    doc["status"] = INTRINSICS_STATUS_VALID if not reasons else INTRINSICS_STATUS_INVALID
    if reasons:
        doc["invalid_reasons"] = reasons
    else:
        doc.pop("invalid_reasons", None)
    return doc


def is_intrinsics_valid(document: dict[str, Any]) -> bool:
    return document.get("status", INTRINSICS_STATUS_INVALID) == INTRINSICS_STATUS_VALID


def read_camera_intrinsics_entry(
    calib: Any,
    socket: Any,
    width: int,
    height: int,
    *,
    oak_socket: str | None = None,
) -> dict[str, Any]:
    """Read one camera entry using Calibration Reader API order."""
    w, h = int(width), int(height)

    eeprom_entry = _read_from_eeprom_camera_data(calib, oak_socket=oak_socket, width=w, height=h)
    if eeprom_entry is not None:
        if oak_socket:
            eeprom_entry["oak_socket"] = oak_socket
        return eeprom_entry

    mat, intrinsics_source, intrinsics_read_ok = _read_intrinsics_matrix(calib, socket, w, h)
    if mat is not None:
        fx, fy, ppx, ppy = _matrix_to_intrinsics(mat)
    else:
        intrinsics_read_ok = False
        intrinsics_source = "fallback"
        fx = fy = float(max(w, h))
        ppx, ppy = w / 2.0, h / 2.0

    coeffs, coeffs_full, distortion_read_ok, distortion_source = _read_distortion_coefficients(
        calib, socket
    )

    entry: dict[str, Any] = {
        "width": w,
        "height": h,
        "fx": fx,
        "fy": fy,
        "ppx": ppx,
        "ppy": ppy,
        "distortion_model": _distortion_model_name(calib, socket),
        "coeffs": coeffs,
        "intrinsics_source": intrinsics_source,
        "distortion_source": distortion_source,
        "eeprom_intrinsics_read_ok": intrinsics_read_ok,
        "eeprom_distortion_read_ok": distortion_read_ok,
    }
    if coeffs_full:
        entry["distortion_coefficients_full"] = coeffs_full
    if oak_socket:
        entry["oak_socket"] = oak_socket
    return entry


def _eeprom_camera_data_count(calib: Any) -> int | None:
    try:
        eeprom = calib.eepromToJson()
        if isinstance(eeprom, dict):
            camera_data = eeprom.get("cameraData")
            if isinstance(camera_data, list):
                return len(camera_data)
    except Exception:
        pass
    return None


def dump_eeprom_json(calib: Any, path: Path) -> Path:
    """Mirror Calibration Reader: calibData.eepromToJsonFile(path)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    calib.eepromToJsonFile(str(path.resolve()))
    return path


def build_camera_intrinsics_document(
    calib: Any,
    *,
    device_mxid: str,
    cameras: dict[str, dict[str, Any]],
    calibration_source: str | None = None,
) -> dict[str, Any]:
    """
    Build meta/camera_intrinsics.json payload.

    ``cameras`` maps lerobot video key -> {socket, width, height, oak_socket?}.
    """
    out_cameras: dict[str, Any] = {}
    for lerobot_key, spec in cameras.items():
        socket = spec["socket"]
        w = int(spec["width"])
        h = int(spec["height"])
        oak_socket = spec.get("oak_socket")
        out_cameras[lerobot_key] = read_camera_intrinsics_entry(
            calib,
            socket,
            w,
            h,
            oak_socket=oak_socket,
        )
    doc: dict[str, Any] = {
        "codebase": INTRINSICS_SCHEMA,
        "device_mxid": device_mxid,
        "cameras": out_cameras,
    }
    if calibration_source:
        doc["calibration_source"] = calibration_source
    eeprom_count = _eeprom_camera_data_count(calib)
    if eeprom_count is not None:
        doc["eeprom_camera_data_count"] = eeprom_count
    return finalize_intrinsics_document(doc)


def write_camera_intrinsics_json(path: Path, document: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    finalized = finalize_intrinsics_document(document)
    path.write_text(json.dumps(finalized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_camera_intrinsics_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def intrinsics_has_valid_distortion(document: dict[str, Any]) -> bool:
    """True when document status is valid (non-zero coeffs on every camera)."""
    if document.get("status") == INTRINSICS_STATUS_INVALID:
        return False
    if document.get("status") == INTRINSICS_STATUS_VALID:
        return True
    return not collect_intrinsics_invalid_reasons(document)
