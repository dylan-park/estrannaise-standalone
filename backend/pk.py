"""PK engine and constants — ported from const.py, all HA dependencies removed."""

from __future__ import annotations

import datetime
import math

# ── Units ─────────────────────────────────────────────────────────────────────

AVAILABLE_UNITS = {
    "pg/mL": {"conversion_factor": 1.0},
    "pmol/L": {"conversion_factor": 3.6713},
}

TARGET_RANGE_LOWER = 100
TARGET_RANGE_UPPER = 200
TARGET_TROUGH: dict[str, float] = {"target_range": 200.0, "menstrual_range": 100.0}

# ── Esters / Methods ──────────────────────────────────────────────────────────

ESTERS: dict[str, str] = {
    "E": "Estradiol",
    "EB": "Estradiol Benzoate",
    "EV": "Estradiol Valerate",
    "EEn": "Estradiol Enanthate",
    "EC": "Estradiol Cypionate",
    "EUn": "Estradiol Undecylate",
}
METHODS: dict[str, str] = {
    "im": "Intramuscular",
    "subq": "Subcutaneous",
    "patch": "Transdermal Patch",
    "oral": "Oral (micronized Estradiol)",
}
ESTER_METHOD_TO_MODEL: dict[tuple[str, str], str] = {
    ("EB", "im"): "EB im",
    ("EV", "im"): "EV im",
    ("EEn", "im"): "EEn im",
    ("EC", "im"): "EC im",
    ("EUn", "im"): "EUn im",
    ("EB", "subq"): "EB im",
    ("EV", "subq"): "EV im",
    ("EEn", "subq"): "EEn im",
    ("EC", "subq"): "EC im",
    ("EUn", "subq"): "EUn casubq",
    ("E", "patch"): "patch",
    ("E", "oral"): "E oral",
}

# ── PK Parameters [d, k1, k2, k3] ────────────────────────────────────────────

PK_PARAMETERS: dict[str, list[float]] = {
    "EB im": [1893.1, 0.67, 61.5, 4.34],
    "EV im": [478.0, 0.236, 4.85, 1.24],
    "EEn im": [191.4, 0.119, 0.601, 0.402],
    "EC im": [246.0, 0.0825, 3.57, 0.669],
    "EUn im": [471.5, 0.01729, 6.528, 2.285],
    "EUn casubq": [16.15, 0.046, 0.022, 0.101],
    "patch tw": [16.792, 0.283, 5.592, 4.3],
    "patch ow": [59.481, 0.107, 7.842, 5.193],
    "E oral": [51.5, 100.0, 8.88, 1.032],
}
PATCH_WEAR_DAYS: dict[str, float] = {"patch tw": 3.5, "patch ow": 7.0}
SUGGESTED_INTERVALS: dict[str, list[float]] = {
    "EB im": [2.0, 3.0],
    "EV im": [3.5, 5.0, 7.0],
    "EEn im": [7.0, 10.0],
    "EC im": [7.0],
    "EUn im": [14.0, 28.0],
    "EUn casubq": [14.0, 28.0],
    "patch tw": [3.5],
    "patch ow": [7.0],
    "E oral": [1.0],
}

# ── Menstrual cycle reference data ────────────────────────────────────────────

MENSTRUAL_CYCLE_DATA: dict[str, list[float]] = {
    "t": list(range(30)),
    "E2": [
        37.99,
        40.59,
        37.49,
        34.99,
        35.49,
        39.54,
        41.99,
        44.34,
        53.43,
        58.58,
        71.43,
        98.92,
        132.31,
        177.35,
        255.88,
        182.80,
        85.23,
        70.98,
        87.97,
        109.92,
        122.77,
        132.56,
        150.30,
        133.81,
        137.16,
        134.96,
        92.73,
        85.68,
        46.34,
        41.19,
    ],
    "E2p5": [
        15.68,
        17.99,
        20.48,
        21.63,
        22.60,
        23.86,
        25.44,
        30.64,
        33.96,
        42.95,
        51.88,
        50.79,
        65.79,
        91.89,
        137.25,
        131.30,
        43.55,
        42.12,
        56.83,
        73.49,
        79.70,
        72.75,
        79.46,
        76.79,
        76.05,
        80.22,
        57.26,
        47.62,
        27.77,
        25.60,
    ],
    "E2p95": [
        52.97,
        51.12,
        51.58,
        54.74,
        53.59,
        57.08,
        61.20,
        60.16,
        72.79,
        85.36,
        94.46,
        133.70,
        218.89,
        314.28,
        413.41,
        388.28,
        140.11,
        108.52,
        135.06,
        181.42,
        191.73,
        196.05,
        189.45,
        195.64,
        208.23,
        219.75,
        174.38,
        148.77,
        135.58,
        188.92,
    ],
}

# ── Helpers ───────────────────────────────────────────────────────────────────


def resolve_model_key(
    ester: str, method: str, interval_days: float = 7.0
) -> str | None:
    key = ESTER_METHOD_TO_MODEL.get((ester, method))
    if key == "patch":
        return "patch tw" if interval_days <= 5.0 else "patch ow"
    return key


# ── Three-compartment PK math ─────────────────────────────────────────────────


def e2_curve_3c(
    t: float, dose: float, d: float, k1: float, k2: float, k3: float
) -> float:
    if t < 0 or dose <= 0 or d <= 0:
        return 0.0
    try:
        if k1 == k2 and k2 == k3:
            return dose * d * k1 * k1 * t * t * math.exp(-k1 * t) / 2.0
        if k1 == k2 and k2 != k3:
            return (
                dose
                * d
                * k1
                * k1
                * (math.exp(-k3 * t) - math.exp(-k1 * t) * (1 + (k1 - k3) * t))
                / (k1 - k3)
                / (k1 - k3)
            )
        if k1 != k2 and k1 == k3:
            return (
                dose
                * d
                * k1
                * k2
                * (math.exp(-k2 * t) - math.exp(-k1 * t) * (1 + (k1 - k2) * t))
                / (k1 - k2)
                / (k1 - k2)
            )
        if k1 != k2 and k2 == k3:
            return (
                dose
                * d
                * k1
                * k2
                * (math.exp(-k1 * t) - math.exp(-k2 * t) * (1 - (k1 - k2) * t))
                / (k1 - k2)
                / (k1 - k2)
            )
        return (
            dose
            * d
            * k1
            * k2
            * (
                math.exp(-k1 * t) / ((k1 - k2) * (k1 - k3))
                - math.exp(-k2 * t) / ((k1 - k2) * (k2 - k3))
                + math.exp(-k3 * t) / ((k1 - k3) * (k2 - k3))
            )
        )
    except (OverflowError, ZeroDivisionError):
        return 0.0


def _es_single_dose_3c(t: float, dose: float, d: float, k1: float, k2: float) -> float:
    if t < 0 or dose <= 0 or d <= 0:
        return 0.0
    if k1 == k2:
        return dose * d * k1 * t * math.exp(-k1 * t)
    return dose * d * k1 / (k1 - k2) * (math.exp(-k2 * t) - math.exp(-k1 * t))


def e2_patch_3c(
    t: float, dose: float, d: float, k1: float, k2: float, k3: float, w: float
) -> float:
    if t < 0:
        return 0.0
    if t <= w:
        return e2_curve_3c(t, dose, d, k1, k2, k3)
    es_w = _es_single_dose_3c(w, dose, d, k1, k2)
    e2_w = e2_curve_3c(w, dose, d, k1, k2, k3)
    ta = t - w
    ret = 0.0
    if es_w > 0:
        if k2 == k3:
            ret += es_w * k2 * ta * math.exp(-k2 * ta)
        else:
            ret += es_w * k2 / (k2 - k3) * (math.exp(-k3 * ta) - math.exp(-k2 * ta))
    if e2_w > 0:
        ret += e2_w * math.exp(-k3 * ta)
    return ret


def compute_e2_at_time(
    t_now: float, doses: list[dict], scaling_factor: float = 1.0
) -> float:
    total = 0.0
    for dose_rec in doses:
        model = dose_rec.get("model", "")
        params = PK_PARAMETERS.get(model)
        if not params:
            continue
        d, k1, k2, k3 = params
        dose_mg = dose_rec.get("dose_mg", 0.0)
        if model in PATCH_WEAR_DAYS:
            dose_mg *= 1000.0
        t_days = (t_now - dose_rec["timestamp"]) / 86400.0
        if model in PATCH_WEAR_DAYS:
            total += e2_patch_3c(t_days, dose_mg, d, k1, k2, k3, PATCH_WEAR_DAYS[model])
        else:
            total += e2_curve_3c(t_days, dose_mg, d, k1, k2, k3)
    return total * scaling_factor


def compute_steady_state_e2_at_time(
    t_target: float, all_configs: list[dict], n: int = 20
) -> float:
    """E2 at t_target assuming steady-state (for blood tests before dose history)."""
    virtual_doses: list[dict] = []
    for cfg in all_configs:
        ester = cfg.get("ester", "")
        method = cfg.get("method", "")
        interval_days = cfg.get("interval_days", 7.0)
        dose_mg = cfg.get("dose_mg", 0.0)
        dose_time_str = cfg.get("dose_time", "00:00")
        model_key = resolve_model_key(ester, method, interval_days)
        if (
            not model_key
            or model_key not in PK_PARAMETERS
            or dose_mg <= 0
            or interval_days <= 0
        ):
            continue
        interval_s = interval_days * 86400.0
        try:
            h, m = map(int, dose_time_str.split(":"))
        except (ValueError, AttributeError):
            h, m = 0, 0
        dt_target = datetime.datetime.fromtimestamp(t_target)
        anchor = dt_target.replace(
            hour=h, minute=m, second=0, microsecond=0
        ).timestamp()
        if anchor > t_target:
            anchor -= 86400
        # Walk to last dose at or before t_target
        far_past = anchor - n * interval_s
        t = far_past
        last_before = far_past
        while t <= t_target:
            last_before = t
            t += interval_s
        anchor = last_before
        for i in range(n):
            virtual_doses.append(
                {
                    "timestamp": anchor - i * interval_s,
                    "model": model_key,
                    "dose_mg": dose_mg,
                }
            )
    if not virtual_doses:
        return 0.0
    return compute_e2_at_time(t_target, virtual_doses, scaling_factor=1.0)


# ── Regimen suggestion ────────────────────────────────────────────────────────


def compute_suggested_regimen(
    ester: str, method: str, target_type: str = "target_range"
) -> dict | None:
    if target_type == "menstrual_range":
        return compute_cycle_fit_regimen(ester, method)
    target_trough = TARGET_TROUGH.get(target_type, 200.0)
    model_key = ESTER_METHOD_TO_MODEL.get((ester, method))
    if model_key is None:
        return None
    if model_key == "patch":
        model_key = "patch tw"
    params = PK_PARAMETERS.get(model_key)
    if not params:
        return None
    d, k1, k2, k3 = params
    for interval in SUGGESTED_INTERVALS.get(model_key, [7.0]):
        if 7.0 / interval > 4.0:
            continue
        trough_per_mg = sum(
            e2_patch_3c(n * interval, 1.0, d, k1, k2, k3, PATCH_WEAR_DAYS[model_key])
            if model_key in PATCH_WEAR_DAYS
            else e2_curve_3c(n * interval, 1.0, d, k1, k2, k3)
            for n in range(1, 60)
        )
        if trough_per_mg <= 0:
            continue
        dose = target_trough / trough_per_mg
        if model_key in PATCH_WEAR_DAYS:
            dose = max(0.025, min(0.4, round(dose / 1000 / 0.025) * 0.025))
        else:
            dose = max(0.5, min(20.0, round(dose * 2) / 2))
        return {"dose_mg": dose, "interval_days": interval, "model_key": model_key}
    return None


# ── Cycle fitting (NNLS) ──────────────────────────────────────────────────────


def _ss_unit_3c(
    t_mod: float, T: float, d: float, k1: float, k2: float, k3: float
) -> float:
    if T <= 0 or d <= 0:
        return 0.0
    try:

        def _g(k: float, t: float) -> float:
            return math.exp(-k * t) / (1.0 - math.exp(-k * T))

        return max(
            0.0,
            d
            * k1
            * k2
            * (
                _g(k1, t_mod) / ((k1 - k2) * (k1 - k3))
                - _g(k2, t_mod) / ((k1 - k2) * (k2 - k3))
                + _g(k3, t_mod) / ((k1 - k3) * (k2 - k3))
            ),
        )
    except (OverflowError, ZeroDivisionError, ValueError):
        return 0.0


def _basis_vector(
    interval: float,
    phase: float,
    d: float,
    k1: float,
    k2: float,
    k3: float,
    n: int = 28,
) -> list[float]:
    return [
        _ss_unit_3c((day - phase) % interval, interval, d, k1, k2, k3)
        for day in range(n)
    ]


def _gauss_solve(A: list[list[float]], b: list[float]) -> list[float] | None:
    n = len(b)
    if n == 0:
        return []
    M = [row[:] + [bi] for row, bi in zip(A, b)]
    for col in range(n):
        mx, mr = abs(M[col][col]), col
        for row in range(col + 1, n):
            if abs(M[row][col]) > mx:
                mx, mr = abs(M[row][col]), row
        if mx < 1e-14:
            return None
        if mr != col:
            M[col], M[mr] = M[mr], M[col]
        for row in range(col + 1, n):
            f = M[row][col] / M[col][col]
            for j in range(col, n + 1):
                M[row][j] -= f * M[col][j]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        if abs(M[i][i]) < 1e-14:
            return None
        x[i] = M[i][n]
        for j in range(i + 1, n):
            x[i] -= M[i][j] * x[j]
        x[i] /= M[i][i]
    return x


def _nnls(columns: list[list[float]], b: list[float]) -> list[float]:
    n, k = len(b), len(columns)
    if k == 0:
        return []
    x = [0.0] * k
    free = [False] * k
    for _ in range(3 * k + 1):
        r = list(b)
        for j in range(k):
            if x[j]:
                for i in range(n):
                    r[i] -= columns[j][i] * x[j]
        w = [sum(columns[j][i] * r[i] for i in range(n)) for j in range(k)]
        best_j, best_w = -1, 1e-10
        for j in range(k):
            if not free[j] and w[j] > best_w:
                best_w, best_j = w[j], j
        if best_j < 0:
            break
        free[best_j] = True
        for _ in range(3 * k + 1):
            fi = [j for j in range(k) if free[j]]
            nf = len(fi)
            AtA = [
                [
                    sum(columns[fi[ii]][i] * columns[fi[jj]][i] for i in range(n))
                    for jj in range(nf)
                ]
                for ii in range(nf)
            ]
            Atb = [sum(columns[fi[ii]][i] * b[i] for i in range(n)) for ii in range(nf)]
            sf = _gauss_solve(AtA, Atb)
            if sf is None:
                break
            if all(v >= 0 for v in sf):
                for ii, j in enumerate(fi):
                    x[j] = sf[ii]
                break
            alpha = 1.0
            for ii, j in enumerate(fi):
                if sf[ii] <= 0 and x[j] > 0:
                    alpha = min(alpha, x[j] / (x[j] - sf[ii]))
            for ii, j in enumerate(fi):
                x[j] += alpha * (sf[ii] - x[j])
                if x[j] <= 1e-12:
                    free[j] = False
                    x[j] = 0.0
    return x


def compute_cycle_fit_regimen(
    ester: str, method: str, max_schedules: int = 4
) -> dict | None:
    model_key = ESTER_METHOD_TO_MODEL.get((ester, method))
    if model_key is None:
        return None
    if model_key == "patch":
        model_key = "patch tw"
    params = PK_PARAMETERS.get(model_key)
    if not params:
        return None
    d, k1, k2, k3 = params
    n_days = 28
    target = MENSTRUAL_CYCLE_DATA["E2"][:n_days]
    interval_set = set(SUGGESTED_INTERVALS.get(model_key, [7.0])) | {
        3.5,
        4.0,
        5.0,
        7.0,
        9.0,
        10.0,
        14.0,
        28.0,
    }
    candidates: list[tuple] = []
    for intv in sorted(interval_set):
        if intv < 2.0 or intv > 28.0:
            continue
        for phase in range(max(1, int(math.ceil(intv)))):
            bv = _basis_vector(intv, float(phase), d, k1, k2, k3, n_days)
            candidates.append((intv, float(phase), bv))
    selected: list[int] = []
    prev_mse = sum(v**2 for v in target) / n_days
    for step in range(max_schedules):
        best_ci, best_mse = -1, prev_mse
        for ci, (_, _, bv) in enumerate(candidates):
            if ci in selected:
                continue
            cols = [candidates[si][2] for si in selected] + [bv]
            x = _nnls(cols, target)
            fitted = [
                sum(x[j] * cols[j][i] for j in range(len(cols))) for i in range(n_days)
            ]
            mse = sum((target[i] - fitted[i]) ** 2 for i in range(n_days)) / n_days
            if mse < best_mse:
                best_mse, best_ci = mse, ci
        if best_ci < 0:
            break
        if step > 0 and prev_mse > 0 and (prev_mse - best_mse) / prev_mse < 0.01:
            break
        selected.append(best_ci)
        prev_mse = best_mse
    if not selected:
        return None
    cols = [candidates[si][2] for si in selected]
    final_x = _nnls(cols, target)
    schedules = []
    for si, dose_raw in zip(selected, final_x):
        if dose_raw < 0.25:
            continue
        intv, phase, _ = candidates[si]
        if model_key in PATCH_WEAR_DAYS:
            dose_mg = max(0.025, min(0.4, round(dose_raw / 1000 / 0.025) * 0.025))
        else:
            dose_mg = max(0.5, min(20.0, round(dose_raw * 2) / 2))
        schedules.append(
            {
                "dose_mg": dose_mg,
                "interval_days": intv,
                "phase_days": phase,
                "model_key": model_key,
            }
        )
    if not schedules:
        return None
    curve = [0.0] * n_days
    for sch in schedules:
        bv = _basis_vector(
            sch["interval_days"], sch["phase_days"], d, k1, k2, k3, n_days
        )
        for i in range(n_days):
            curve[i] += sch["dose_mg"] * bv[i]
    rms = math.sqrt(sum((target[i] - curve[i]) ** 2 for i in range(n_days)) / n_days)
    return {
        "schedules": schedules,
        "residual_rms": round(rms, 2),
        "cycle_fit_curve": [round(v, 1) for v in curve],
    }
