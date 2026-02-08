/**
 * Estrannaise HRT Monitor - Main Graph Card
 *
 * Displays estimated blood estradiol levels over time using Plotly.js,
 * with pharmacokinetic models ported from estrannaise.js.
 */

const CARD_VERSION = '3.0.0';

// ── Color helpers for ha-form color_rgb selector ────────────────────────────

function hexToRgb(hex) {
  const m = (hex || '').replace('#', '').match(/.{1,2}/g);
  if (!m || m.length < 3) return [233, 30, 99];
  return [parseInt(m[0], 16), parseInt(m[1], 16), parseInt(m[2], 16)];
}

function rgbToHex(rgb) {
  if (!Array.isArray(rgb) || rgb.length < 3) return '#E91E63';
  return '#' + rgb.slice(0, 3).map(c =>
    Math.max(0, Math.min(255, Math.round(c))).toString(16).padStart(2, '0')
  ).join('');
}

// ── PK Engine (ported from estrannaise.js src/models.js) ────────────────────

function e2Curve3C(t, dose, d, k1, k2, k3) {
  if (t < 0 || dose <= 0 || d <= 0) return 0;
  try {
    if (k1 === k2 && k2 === k3) {
      return dose * d * k1 * k1 * t * t * Math.exp(-k1 * t) / 2;
    }
    if (k1 === k2 && k2 !== k3) {
      return dose * d * k1 * k1 *
        (Math.exp(-k3 * t) - Math.exp(-k1 * t) * (1 + (k1 - k3) * t)) /
        ((k1 - k3) * (k1 - k3));
    }
    if (k1 !== k2 && k1 === k3) {
      return dose * d * k1 * k2 *
        (Math.exp(-k2 * t) - Math.exp(-k1 * t) * (1 + (k1 - k2) * t)) /
        ((k1 - k2) * (k1 - k2));
    }
    if (k1 !== k2 && k2 === k3) {
      return dose * d * k1 * k2 *
        (Math.exp(-k1 * t) - Math.exp(-k2 * t) * (1 - (k1 - k2) * t)) /
        ((k1 - k2) * (k1 - k2));
    }
    return dose * d * k1 * k2 * (
      Math.exp(-k1 * t) / ((k1 - k2) * (k1 - k3)) -
      Math.exp(-k2 * t) / ((k1 - k2) * (k2 - k3)) +
      Math.exp(-k3 * t) / ((k1 - k3) * (k2 - k3))
    );
  } catch (e) {
    return 0;
  }
}

function esSingleDose3C(t, dose, d, k1, k2) {
  if (t < 0 || dose <= 0 || d <= 0) return 0;
  if (k1 === k2) return dose * d * k1 * t * Math.exp(-k1 * t);
  return dose * d * k1 / (k1 - k2) * (Math.exp(-k2 * t) - Math.exp(-k1 * t));
}

function e2Patch3C(t, dose, d, k1, k2, k3, W) {
  if (t < 0) return 0;
  if (t <= W) return e2Curve3C(t, dose, d, k1, k2, k3);
  const esW = esSingleDose3C(W, dose, d, k1, k2);
  const e2W = e2Curve3C(W, dose, d, k1, k2, k3);
  const tAfter = t - W;
  let ret = 0;
  if (esW > 0) {
    if (k2 === k3) {
      ret += esW * k2 * tAfter * Math.exp(-k2 * tAfter);
    } else {
      ret += esW * k2 / (k2 - k3) * (Math.exp(-k3 * tAfter) - Math.exp(-k2 * tAfter));
    }
  }
  if (e2W > 0) ret += e2W * Math.exp(-k3 * tAfter);
  return ret;
}

function computeE2(tDays, dose, model, pkParams, patchWearDays) {
  const params = pkParams[model];
  if (!params) return 0;
  const [d, k1, k2, k3] = params;
  if (patchWearDays && patchWearDays[model] !== undefined) {
    // Patch PK params are calibrated for mcg/day; stored dose is mg/day
    return e2Patch3C(tDays, dose * 1000, d, k1, k2, k3, patchWearDays[model]);
  }
  return e2Curve3C(tDays, dose, d, k1, k2, k3);
}

// ── Ester/method → internal model key resolver ──────────────────────────────

const ESTER_METHOD_COMBO = {
  'EB|im': 'EB im',
  'EV|im': 'EV im',
  'EEn|im': 'EEn im',
  'EC|im': 'EC im',
  'EUn|im': 'EUn im',
  'EB|subq': 'EB im',
  'EV|subq': 'EV im',
  'EEn|subq': 'EEn im',
  'EC|subq': 'EC im',
  'EUn|subq': 'EUn casubq',
  'E|patch': 'patch', // resolved below based on interval
  'E|oral': 'E oral',
};

function resolveModelKey(ester, method, intervalDays) {
  const key = ESTER_METHOD_COMBO[`${ester}|${method}`];
  if (key === 'patch') {
    return intervalDays <= 5 ? 'patch tw' : 'patch ow';
  }
  return key || null;
}

// ── Dose time alignment helper ──────────────────────────────────────────────

function alignToTimeOfDay(nowSec, doseTime, intervalSec) {
  // Parse "HH:MM" string to hours/minutes
  let hour = 8, minute = 0;
  if (doseTime) {
    const parts = doseTime.split(':');
    hour = parseInt(parts[0], 10) || 8;
    minute = parseInt(parts[1], 10) || 0;
  }
  // Find today's dose time (local timezone, not UTC)
  const nowDate = new Date(nowSec * 1000);
  const todayDose = new Date(nowDate);
  todayDose.setHours(hour, minute, 0, 0);
  const todayTs = todayDose.getTime() / 1000;
  // Anchor: most recent aligned dose time
  return todayTs > nowSec ? todayTs - intervalSec : todayTs;
}

// ── Cubic spline interpolation for menstrual cycle ──────────────────────────

class CubicSpline {
  constructor(xs, ys) {
    const n = xs.length;
    this.xs = xs;
    this.ys = ys;
    if (n < 2) {
      this.b = []; this.c = []; this.d = [];
      return;
    }
    const h = [], alpha = [], l = [], mu = [], z = [];
    const c = new Array(n).fill(0);
    const b = new Array(n).fill(0);
    const d = new Array(n).fill(0);
    for (let i = 0; i < n - 1; i++) h[i] = xs[i + 1] - xs[i];
    for (let i = 1; i < n - 1; i++) {
      alpha[i] = (3 / h[i]) * (ys[i + 1] - ys[i]) - (3 / h[i - 1]) * (ys[i] - ys[i - 1]);
    }
    l[0] = 1; mu[0] = 0; z[0] = 0;
    for (let i = 1; i < n - 1; i++) {
      l[i] = 2 * (xs[i + 1] - xs[i - 1]) - h[i - 1] * mu[i - 1];
      mu[i] = h[i] / l[i];
      z[i] = (alpha[i] - h[i - 1] * z[i - 1]) / l[i];
    }
    l[n - 1] = 1; z[n - 1] = 0; c[n - 1] = 0;
    for (let j = n - 2; j >= 0; j--) {
      c[j] = z[j] - mu[j] * c[j + 1];
      b[j] = (ys[j + 1] - ys[j]) / h[j] - h[j] * (c[j + 1] + 2 * c[j]) / 3;
      d[j] = (c[j + 1] - c[j]) / (3 * h[j]);
    }
    this.b = b; this.c = c; this.d = d;
  }

  at(x) {
    const xs = this.xs, ys = this.ys;
    if (xs.length < 2) return ys.length > 0 ? ys[0] : 0;
    let i = xs.length - 2;
    for (let j = 0; j < xs.length - 1; j++) {
      if (x < xs[j + 1]) { i = j; break; }
    }
    const dx = x - xs[i];
    return ys[i] + this.b[i] * dx + this.c[i] * dx * dx + this.d[i] * dx * dx * dx;
  }
}

// ── Card implementation ─────────────────────────────────────────────────────

if (!customElements.get('estrannaise-card')) {

  class EstrannaisCard extends HTMLElement {

    static getConfigElement() {
      return document.createElement('estrannaise-card-editor');
    }

    static getStubConfig() {
      return {
        entity: 'sensor.estrannaise_my_hrt',
        title: 'Estradiol Level',
        icon: 'mdi:chart-bell-curve-cumulative',
        days_to_show: 30,
        days_to_predict: 7,
        show_target_range: true,
        show_menstrual_cycle: false,
        show_dose_chevrons: true,
      };
    }

    constructor() {
      super();
      this._plotlyLoaded = false;
      this._plotEl = null;
      this._resizeObserver = null;
    }

    setConfig(config) {
      if (!config.entity) throw new Error('Please define an entity');
      const base = {
        days_to_show: 30,
        days_to_predict: 7,
        show_target_range: true,
        show_danger_threshold: false,
        show_menstrual_cycle: false,
        show_dose_chevrons: true,
        line_color: '#E91E63',
        target_color: 'rgba(33,150,243,0.13)',
        danger_color: 'rgba(244,67,54,0.10)',
        cycle_color: 'rgba(156,39,176,0.08)',
        dose_marker_color: 'rgba(33,150,243,0.6)',
        test_marker_color: '#F44336',
        ...config,
      };
      // Backward compat: convert legacy hours_* to days_*
      if ('hours_to_show' in config && !('days_to_show' in config)) {
        base.days_to_show = Math.max(1, Math.round(config.hours_to_show / 24));
      }
      if ('hours_to_predict' in config && !('days_to_predict' in config)) {
        base.days_to_predict = Math.max(1, Math.round(config.hours_to_predict / 24));
      }
      // Default prediction_color to line_color if not explicitly set
      if (!config.prediction_color) {
        base.prediction_color = base.line_color;
      }
      this.config = base;
    }

    set hass(hass) {
      this._hass = hass;
      if (!this.shadowRoot) this._buildShadow();
      this._update();
    }

    _buildShadow() {
      this.attachShadow({ mode: 'open' });
      const style = document.createElement('style');
      style.textContent = `
        :host {
          display: block;
        }
        .card {
          background: var(--ha-card-background, var(--card-background-color, #fff));
          border-radius: var(--ha-card-border-radius, 12px);
          box-shadow: var(--ha-card-box-shadow, 0 2px 6px rgba(0,0,0,0.1));
          padding: 16px;
          overflow: hidden;
        }
        .header {
          font-size: 16px;
          font-weight: 500;
          color: var(--primary-text-color, #212121);
          margin-bottom: 8px;
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .header ha-icon {
          --mdc-icon-size: 20px;
          color: var(--primary-color);
        }
        .value {
          font-size: 28px;
          font-weight: 300;
          color: var(--primary-text-color);
          margin-bottom: 12px;
        }
        .value .unit {
          font-size: 14px;
          color: var(--secondary-text-color, #757575);
        }
        .plot-container {
          width: 100%;
          min-height: 300px;
          position: relative;
        }
        .dose-spike {
          position: absolute;
          width: 0;
          border-left: 1.5px dashed var(--secondary-text-color, rgba(0,0,0,0.3));
          pointer-events: none;
          opacity: 0;
          transition: opacity 0.15s ease;
          z-index: 10;
        }
        .dose-spike.visible {
          opacity: 1;
        }
        .dose-spike-label {
          position: absolute;
          pointer-events: none;
          opacity: 0;
          transition: opacity 0.15s ease;
          z-index: 11;
          background: var(--ha-card-background, var(--card-background-color, #fff));
          border: 1px solid var(--divider-color, #ccc);
          border-radius: 4px;
          padding: 2px 6px;
          font-size: 11px;
          color: var(--primary-text-color, #212121);
          white-space: pre-line;
          text-align: center;
          transform: translateX(-50%);
        }
        .dose-spike-label.visible {
          opacity: 1;
        }
        .loading {
          text-align: center;
          padding: 40px;
          color: var(--secondary-text-color);
        }
        .disclaimer {
          font-size: 10px;
          color: var(--secondary-text-color, #757575);
          margin-top: 8px;
          line-height: 1.3;
          opacity: 0.7;
        }
        .suggested-regimen {
          font-size: 12px;
          color: var(--secondary-text-color, #757575);
          margin-top: 4px;
          font-style: italic;
        }
      `;

      const card = document.createElement('div');
      card.className = 'card';
      const titleText = this.config.title || 'Estradiol Level';
      const iconName = this.config.icon || 'mdi:chart-bell-curve-cumulative';
      card.innerHTML = `
        <div class="header">
          <ha-icon icon="${iconName}"></ha-icon>
          <span></span>
        </div>
        <div class="value"><span class="e2-value">--</span> <span class="unit"></span></div>
        <div class="suggested-regimen" style="display:none"></div>
        <div class="plot-container"><div class="loading">Loading chart...</div></div>
        <div class="disclaimer">Estimated levels are pharmacokinetic approximations based on population models, not actual blood serum measurements. Always confirm with blood tests.</div>
      `;

      this.shadowRoot.appendChild(style);
      this.shadowRoot.appendChild(card);
      card.querySelector('.header span').textContent = titleText;
      this._plotEl = card.querySelector('.plot-container');
    }

    async _ensurePlotly() {
      if (this._plotlyLoaded) return true;
      if (window.Plotly) {
        this._plotlyLoaded = true;
        return true;
      }
      // Global pending promise prevents duplicate <script> tags
      if (!window._estrannaisePlotlyLoading) {
        window._estrannaisePlotlyLoading = new Promise((resolve) => {
          const script = document.createElement('script');
          script.src = '/estrannaise/plotly-2.35.2.min.js';
          script.onload = () => resolve(true);
          script.onerror = () => {
            console.error('Failed to load Plotly.js');
            resolve(false);
          };
          document.head.appendChild(script);
        });
      }
      const ok = await window._estrannaisePlotlyLoading;
      this._plotlyLoaded = ok;
      return ok;
    }

    async _update() {
      if (!this._hass || !this.config || !this.shadowRoot) return;

      const entity = this._hass.states[this.config.entity];
      if (!entity) return;

      // Skip render if entity state hasn't changed since last render
      const entityKey = entity.last_updated || entity.state;
      if (this._lastEntityKey === entityKey) return;
      this._lastEntityKey = entityKey;

      const attrs = entity.attributes || {};
      const state = entity.state;

      // Update header value
      const valEl = this.shadowRoot.querySelector('.e2-value');
      const unitEl = this.shadowRoot.querySelector('.unit');
      if (valEl) valEl.textContent = state !== 'unknown' && state !== 'unavailable' ? state : '--';
      if (unitEl) unitEl.textContent = attrs.units || 'pg/mL';

      // Show suggested regimen if auto_regimen is enabled
      const suggestedEl = this.shadowRoot.querySelector('.suggested-regimen');
      if (suggestedEl) {
        const suggested = attrs.suggested_regimen;
        if (attrs.auto_regimen && suggested) {
          const esters = attrs.esters || {};
          const methods = attrs.methods || {};
          const firstCfg = (attrs.all_configs || [])[0] || {};
          const esterKey = firstCfg.ester || (attrs.model || '').split(' ')[0];
          const esterName = esters[esterKey] || attrs.model;
          const methodName = methods[attrs.method] || attrs.method;
          const doseUnit = attrs.method === 'patch' ? 'mcg/day' : 'mg';
          if (suggested.schedules) {
            const parts = suggested.schedules.map(s =>
              `${s.dose_mg}${doseUnit}/${s.interval_days}d`
            );
            suggestedEl.textContent = `Auto-suggested: ${parts.join(' + ')} (${esterName}, ${methodName})`;
          } else {
            suggestedEl.textContent = `Auto-suggested: ${suggested.dose_mg}${doseUnit} every ${suggested.interval_days} days (${esterName}, ${methodName})`;
          }
          suggestedEl.style.display = '';
        } else {
          suggestedEl.style.display = 'none';
        }
      }

      const ready = await this._ensurePlotly();
      if (!ready) {
        this._plotEl.innerHTML = '<div class="loading">Failed to load chart library</div>';
        return;
      }

      this._renderPlot(attrs);
    }

    _renderPlot(attrs) {
      const pkParams = attrs.pk_parameters || {};
      const patchWearDays = attrs.patch_wear_days || {};
      const scalingFactor = attrs.scaling_factor || 1.0;
      const scalingVariance = attrs.scaling_variance || 0.0;
      const manualDoses = attrs.doses || [];
      const bloodTests = attrs.blood_tests || [];
      const targetRange = attrs.target_range || { lower: 100, upper: 200 };
      const cycleData = attrs.menstrual_cycle_data || null;
      const allConfigs = attrs.all_configs || [];
      const units = attrs.units || 'pg/mL';

      // Unit conversion factor
      const cf = units === 'pmol/L' ? 3.6713 : 1.0;

      const now = Date.now() / 1000;
      const daysBack = this.config.days_to_show;
      const daysForward = this.config.days_to_predict;
      const tMin = now - daysBack * 86400;
      const tMax = now + daysForward * 86400;

      // ── Collect all dose events ────────────────────────────────────────
      let allDoses = [];

      // Manual doses from entity attributes (all entries)
      for (const d of manualDoses) {
        allDoses.push({
          timestamp: d.timestamp,
          model: d.model,
          dose_mg: d.dose_mg,
          source: 'manual',
        });
      }

      // Automatic recurring doses from ALL entry configs
      const suggestedRegimen = attrs.suggested_regimen || null;
      const cycleFitRegimen = attrs.cycle_fit_regimen || null;

      for (const cfg of allConfigs) {
        const cfgMode = cfg.mode || 'manual';
        if (cfgMode !== 'automatic' && cfgMode !== 'both') continue;

        const ester = cfg.ester || '';
        const method = cfg.method || 'im';
        let cfgDoseMg = cfg.dose_mg || 0;
        let cfgInterval = cfg.interval_days || 7;
        const doseTime = cfg.dose_time || '08:00';

        if (cfg.auto_regimen && cycleFitRegimen && cycleFitRegimen.schedules) {
          // Multi-schedule cycle fit: generate per-schedule auto-doses
          const epochDayNow = Math.floor(now / 86400);
          const cycleDayNow = ((epochDayNow % 28) + 28) % 28;
          let hour = 8, minute = 0;
          if (doseTime) {
            const parts = doseTime.split(':');
            hour = parseInt(parts[0], 10) || 8;
            minute = parseInt(parts[1], 10) || 0;
          }
          const todSec = hour * 3600 + minute * 60;

          for (const sch of cycleFitRegimen.schedules) {
            const schIntervalSec = sch.interval_days * 86400;
            const schPhase = Math.floor(sch.phase_days);
            const daysBack = ((cycleDayNow - schPhase) % 28 + 28) % 28;
            const anchorDay = epochDayNow - daysBack;
            let t = anchorDay * 86400 + todSec;
            while (t <= now) t += schIntervalSec;
            while (t <= tMax) {
              allDoses.push({
                timestamp: t,
                model: sch.model_key,
                dose_mg: sch.dose_mg,
                source: 'automatic',
              });
              t += schIntervalSec;
            }
          }
        } else {
          // Single schedule
          if (cfg.auto_regimen && suggestedRegimen && !suggestedRegimen.schedules) {
            cfgDoseMg = suggestedRegimen.dose_mg || cfgDoseMg;
            cfgInterval = suggestedRegimen.interval_days || cfgInterval;
          }

          const intervalSec = cfgInterval * 86400;
          const modelKey = resolveModelKey(ester, method, cfgInterval);
          if (!modelKey) continue;

          const phaseDays = cfg.phase_days || 0;
          if (phaseDays > 0) {
            // Phase-based anchoring (28-day cycle alignment)
            const epochDayNow = Math.floor(now / 86400);
            const cycleDayNow = ((epochDayNow % 28) + 28) % 28;
            let hour = 8, minute = 0;
            if (doseTime) {
              const parts = doseTime.split(':');
              hour = parseInt(parts[0], 10) || 8;
              minute = parseInt(parts[1], 10) || 0;
            }
            const todSec = hour * 3600 + minute * 60;
            const daysBack = ((cycleDayNow - Math.floor(phaseDays)) % 28 + 28) % 28;
            const anchorDay = epochDayNow - daysBack;
            let t = anchorDay * 86400 + todSec;
            while (t <= now) t += intervalSec;
            while (t <= tMax) {
              allDoses.push({
                timestamp: t,
                model: modelKey,
                dose_mg: cfgDoseMg,
                source: 'automatic',
              });
              t += intervalSec;
            }
          } else {
            // Standard anchoring (today's dose time)
            const anchor = alignToTimeOfDay(now, doseTime, intervalSec);
            let t = anchor + intervalSec;
            while (t <= tMax) {
              allDoses.push({
                timestamp: t,
                model: modelKey,
                dose_mg: cfgDoseMg,
                source: 'automatic',
              });
              t += intervalSec;
            }
          }
        }
      }

      // Card YAML override/supplement doses (future only, for prediction)
      if (this.config.doses && Array.isArray(this.config.doses)) {
        for (const cardDose of this.config.doses) {
          const intervalSec = (cardDose.interval_days || 7) * 86400;
          let t = now + intervalSec;
          while (t <= tMax) {
            allDoses.push({
              timestamp: t,
              model: cardDose.model,
              dose_mg: cardDose.dose,
              source: 'card_yaml',
            });
            t += intervalSec;
          }
        }
      }

      // ── Compute E2 curve ───────────────────────────────────────────────
      const numPoints = 500;
      const step = (tMax - tMin) / (numPoints - 1);
      const histX = [], histY = [], predX = [], predY = [];
      const bandUpperX = [], bandUpperY = [], bandLowerX = [], bandLowerY = [];

      // Blood test baseline (zero-state handling only)
      // When the PK model predicts ~0 at all blood test times, multiplicative
      // scaling can't work. In that case, the blood test level is used as a
      // decaying offset — it represents E2 from sources the model can't
      // explain (endogenous production, unlogged doses, etc.).
      // Decays exponentially (λ=0.02/day, ~35d half-life) so old tests fade.
      const baselineE2 = attrs.baseline_e2 || 0;
      const baselineTestTs = attrs.baseline_test_ts || 0;
      const baselineDecay = (tSec) => {
        const ageDays = (tSec - baselineTestTs) / 86400;
        return baselineE2 * Math.exp(-0.02 * Math.max(0, ageDays));
      };

      // Confidence band multipliers (if variance > 0 and enough blood tests)
      const showBands = scalingVariance > 0 && bloodTests.length >= 2;
      const sqrtVar = Math.sqrt(scalingVariance);

      for (let i = 0; i < numPoints; i++) {
        const t = tMin + i * step;
        const tDate = new Date(t * 1000);
        let e2raw = 0;
        for (const dose of allDoses) {
          const tDays = (t - dose.timestamp) / 86400;
          e2raw += computeE2(tDays, dose.dose_mg, dose.model, pkParams, patchWearDays);
        }
        let e2 = e2raw * scalingFactor * cf;

        // Add decaying baseline offset (after blood test time)
        if (baselineE2 > 0 && t >= baselineTestTs) {
          e2 += baselineDecay(t) * cf;
        }

        if (t <= now) {
          histX.push(tDate);
          histY.push(Math.max(0, e2));
        } else {
          predX.push(tDate);
          predY.push(Math.max(0, e2));
        }

        if (showBands) {
          const upper = e2raw * (scalingFactor + 2 * sqrtVar) * cf;
          const lower = e2raw * Math.max(0, scalingFactor - 2 * sqrtVar) * cf;
          if (t <= now) {
            bandUpperX.push(tDate);
            bandUpperY.push(Math.max(0, upper));
            bandLowerX.push(tDate);
            bandLowerY.push(Math.max(0, lower));
          }
        }
      }

      // Ensure continuity: add current point to both traces
      const nowDate = new Date(now * 1000);
      let e2NowRaw = 0;
      for (const dose of allDoses) {
        const tDays = (now - dose.timestamp) / 86400;
        e2NowRaw += computeE2(tDays, dose.dose_mg, dose.model, pkParams, patchWearDays);
      }
      let e2Now = e2NowRaw * scalingFactor * cf;
      if (baselineE2 > 0 && now >= baselineTestTs) e2Now += baselineDecay(now) * cf;
      histX.push(nowDate);
      histY.push(Math.max(0, e2Now));
      predX.unshift(nowDate);
      predY.unshift(Math.max(0, e2Now));

      // ── Build Plotly traces ────────────────────────────────────────────
      const traces = [];

      // Confidence band (behind the main line)
      if (showBands && bandUpperX.length > 0) {
        // Parse line_color to create semi-transparent version
        const bandColor = this.config.line_color.startsWith('#')
          ? this.config.line_color + '20'
          : 'rgba(233,30,99,0.12)';
        traces.push({
          x: bandUpperX.concat([...bandLowerX].reverse()),
          y: bandUpperY.concat([...bandLowerY].reverse()),
          fill: 'toself',
          fillcolor: bandColor,
          line: { width: 0 },
          type: 'scatter',
          mode: 'lines',
          name: 'Confidence band',
          hoverinfo: 'skip',
          showlegend: true,
        });
      }

      // Historical E2 (solid line with dots)
      traces.push({
        x: histX,
        y: histY,
        type: 'scatter',
        mode: 'lines+markers',
        name: 'Estimated E2',
        line: { color: this.config.line_color, width: 2.5 },
        marker: { size: 2.5, color: this.config.line_color, maxdisplayed: 60 },
        hovertemplate: '<b>%{y:.0f}</b> ' + units + '<br>%{x}<extra></extra>',
      });

      // Predicted E2 (dashed line with dots)
      traces.push({
        x: predX,
        y: predY,
        type: 'scatter',
        mode: 'lines+markers',
        name: 'Projected E2',
        line: { color: this.config.prediction_color, width: 2, dash: 'dot' },
        marker: { size: 2.5, color: this.config.prediction_color, maxdisplayed: 30 },
        hovertemplate: '<b>%{y:.0f}</b> ' + units + ' (projected)<br>%{x}<extra></extra>',
      });

      // Target range (shaded band)
      if (this.config.show_target_range) {
        const lower = targetRange.lower * cf;
        const upper = targetRange.upper * cf;
        traces.push({
          x: [new Date(tMin * 1000), new Date(tMax * 1000), new Date(tMax * 1000), new Date(tMin * 1000)],
          y: [lower, lower, upper, upper],
          fill: 'toself',
          fillcolor: this.config.target_color,
          line: { width: 0 },
          type: 'scatter',
          mode: 'lines',
          name: `Target (${targetRange.lower}-${targetRange.upper} pg/mL)`,
          hoverinfo: 'skip',
          showlegend: true,
        });
      }

      // Danger threshold (>500 pg/mL shaded band)
      if (this.config.show_danger_threshold) {
        const dangerLower = 500 * cf;
        const dangerUpper = 99999 * cf; // extend to top of chart
        traces.push({
          x: [new Date(tMin * 1000), new Date(tMax * 1000), new Date(tMax * 1000), new Date(tMin * 1000)],
          y: [dangerLower, dangerLower, dangerUpper, dangerUpper],
          fill: 'toself',
          fillcolor: this.config.danger_color,
          line: { width: 0 },
          type: 'scatter',
          mode: 'lines',
          name: 'Danger (>500 pg/mL)',
          hoverinfo: 'skip',
          showlegend: true,
        });
      }

      // Menstrual cycle overlay
      if (this.config.show_menstrual_cycle && cycleData) {
        const splineMean = new CubicSpline(cycleData.t, cycleData.E2);
        const splineP5 = new CubicSpline(cycleData.t, cycleData.E2p5);
        const splineP95 = new CubicSpline(cycleData.t, cycleData.E2p95);
        const cycleX = [], cycleP5 = [], cycleP95 = [], cycleMean = [];
        for (let i = 0; i < numPoints; i++) {
          const t = tMin + i * step;
          const tDate = new Date(t * 1000);
          const dayInCycle = (((t / 86400) % 28) + 28) % 28;
          cycleX.push(tDate);
          cycleMean.push(splineMean.at(dayInCycle) * cf);
          cycleP5.push(splineP5.at(dayInCycle) * cf);
          cycleP95.push(splineP95.at(dayInCycle) * cf);
        }
        traces.push({
          x: cycleX.concat([...cycleX].reverse()),
          y: cycleP5.concat([...cycleP95].reverse()),
          fill: 'toself',
          fillcolor: this.config.cycle_color,
          line: { width: 0 },
          type: 'scatter',
          mode: 'lines',
          name: 'Menstrual cycle (p5-p95)',
          hoverinfo: 'skip',
          showlegend: true,
        });
        traces.push({
          x: cycleX,
          y: cycleMean,
          type: 'scatter',
          mode: 'lines',
          name: 'Cycle mean',
          line: { color: 'rgba(156,39,176,0.4)', width: 1, dash: 'dash' },
          hovertemplate: '<b>%{y:.0f}</b> ' + units + ' (cycle)<extra></extra>',
        });
      }

      // Blood test markers
      if (bloodTests.length > 0) {
        const testX = [], testY = [], testText = [];
        for (const test of bloodTests) {
          const ts = test.timestamp;
          if (ts < tMin || ts > tMax) continue;
          testX.push(new Date(ts * 1000));
          testY.push(test.level_pg_ml * cf);
          const safeNotes = test.notes
            ? test.notes.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
            : '';
          testText.push(
            `<b>${(test.level_pg_ml * cf).toFixed(0)}</b> ${units}` +
            (safeNotes ? `<br>${safeNotes}` : '') +
            `<br>ID: ${test.id}`
          );
        }
        if (testX.length > 0) {
          traces.push({
            x: testX,
            y: testY,
            type: 'scatter',
            mode: 'markers',
            name: 'Blood tests',
            marker: {
              color: this.config.test_marker_color,
              size: 10,
              symbol: 'circle',
              line: { color: '#fff', width: 2 },
            },
            hovertemplate: '%{text}<extra></extra>',
            text: testText,
          });
        }
      }

      // Merge coincident doses of the same ester for display (sum dose_mg)
      const esters = attrs.esters || {};
      const MERGE_WINDOW = 3600; // seconds — doses within 1 hour count as same time
      const mergedDoses = [];
      const sorted = [...allDoses].sort((a, b) => a.timestamp - b.timestamp);
      for (const dose of sorted) {
        const last = mergedDoses.length > 0 ? mergedDoses[mergedDoses.length - 1] : null;
        if (last && Math.abs(dose.timestamp - last.timestamp) < MERGE_WINDOW
            && dose.model === last.model) {
          last.dose_mg += dose.dose_mg;
          if (dose.source === 'manual') last.source = 'manual';
        } else {
          mergedDoses.push({ ...dose });
        }
      }

      // Dose event chevrons at bottom of chart (merged display doses)
      if (this.config.show_dose_chevrons !== false) {
        const manualChevX = [], manualChevY = [], manualChevText = [];
        const autoChevX = [], autoChevY = [], autoChevText = [];
        for (const dose of mergedDoses) {
          if (dose.timestamp < tMin || dose.timestamp > tMax) continue;
          const x = new Date(dose.timestamp * 1000);
          const modelParts = (dose.model || '').split(' ');
          const esterName = esters[modelParts[0]] || dose.model;
          const label = `${dose.dose_mg}mg ${esterName}`;
          if (dose.source === 'manual') {
            manualChevX.push(x);
            manualChevY.push(0);
            manualChevText.push(label);
          } else {
            autoChevX.push(x);
            autoChevY.push(0);
            autoChevText.push(label + ' (scheduled)');
          }
        }
        if (manualChevX.length > 0) {
          traces.push({
            x: manualChevX,
            y: manualChevY,
            type: 'scatter',
            mode: 'markers',
            name: 'Manual doses',
            marker: {
              color: this.config.dose_marker_color,
              size: 12,
              symbol: 'triangle-up',
            },
            hovertemplate: '<b>%{text}</b><br>%{x|%b %d %H:%M}<extra></extra>',
            text: manualChevText,
            cliponaxis: false,
          });
        }
        if (autoChevX.length > 0) {
          traces.push({
            x: autoChevX,
            y: autoChevY,
            type: 'scatter',
            mode: 'markers',
            name: 'Scheduled doses',
            marker: {
              color: this.config.dose_marker_color,
              size: 10,
              symbol: 'triangle-up',
              opacity: 0.4,
            },
            hovertemplate: '<b>%{text}</b><br>%{x|%b %d %H:%M}<extra></extra>',
            text: autoChevText,
            cliponaxis: false,
          });
        }
      }

      // ── Layout ─────────────────────────────────────────────────────────
      const isDark = document.documentElement.getAttribute('data-theme') === 'dark' ||
        getComputedStyle(document.documentElement).getPropertyValue('--primary-background-color').trim().match(/^#[0-3]/);

      const textColor = isDark ? '#e0e0e0' : '#424242';
      const gridColor = isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.06)';
      const bgColor = 'rgba(0,0,0,0)';
      const nowLineColor = isDark ? 'rgba(255,255,255,0.4)' : 'rgba(0,0,0,0.3)';

      // Compute y-axis range for "Now" line and layout
      const dataMax = Math.max(...histY, ...predY, (targetRange.upper || 200) * cf);
      const yAxisMax = dataMax * 1.15;

      // "Now" vertical dotted line (as trace to render within plot area)
      traces.push({
        x: [nowDate, nowDate, nowDate],
        y: [0, yAxisMax, yAxisMax],
        type: 'scatter',
        mode: 'lines+text',
        name: 'Now',
        line: { color: nowLineColor, width: 1.5, dash: 'dot' },
        text: ['', '', 'Now'],
        textposition: 'top center',
        textfont: { color: nowLineColor, size: 10 },
        hoverinfo: 'skip',
        showlegend: false,
        cliponaxis: false,
      });

      const layout = {
        autosize: true,
        height: 300,
        margin: { l: 50, r: 20, t: 20, b: 40 },
        paper_bgcolor: bgColor,
        plot_bgcolor: bgColor,
        font: { color: textColor, size: 12 },
        xaxis: {
          type: 'date',
          gridcolor: gridColor,
          linecolor: gridColor,
          tickformat: '%b %d\n%H:%M',
          range: [new Date(tMin * 1000), new Date(tMax * 1000)],
          fixedrange: true,
        },
        yaxis: {
          title: units,
          gridcolor: gridColor,
          linecolor: gridColor,
          range: [0, yAxisMax],
          fixedrange: true,
        },
        showlegend: false,
        annotations: [],
        hovermode: 'closest',
        hoverdistance: 50,
        dragmode: false,
      };

      const plotConfig = {
        displayModeBar: false,
        responsive: true,
        scrollZoom: false,
      };

      // ── Render ─────────────────────────────────────────────────────────
      if (this._plotEl) {
        if (this._plotEl.querySelector('.loading')) {
          this._plotEl.innerHTML = '';
        }
        const plotDiv = this._plotEl.querySelector('.js-plotly-plot') || this._plotEl;

        if (this._plotEl.querySelector('.js-plotly-plot')) {
          window.Plotly.react(plotDiv, traces, layout, plotConfig);
        } else {
          window.Plotly.newPlot(this._plotEl, traces, layout, plotConfig);
        }

        if (!this._resizeObserver) {
          this._resizeObserver = new ResizeObserver(() => {
            if (this._plotEl && window.Plotly) {
              window.Plotly.Plots.resize(this._plotEl);
            }
          });
          this._resizeObserver.observe(this._plotEl);
        }

        // ── Dose spike lines (proximity-triggered vertical lines at dose positions) ──
        if (this.config.show_dose_chevrons !== false) {
          this._setupDoseSpikes(layout.margin, tMin, tMax, yAxisMax, mergedDoses, allDoses, pkParams, patchWearDays, scalingFactor, cf, units, baselineE2, baselineTestTs, now, esters);
        }
      }
    }

    _setupDoseSpikes(margin, tMin, tMax, yMax, displayDoses, allDoses, pkParams, patchWearDays, scalingFactor, cf, units, baselineE2, baselineTestTs, nowSec, esters) {
      if (!this._plotEl) return;

      // Remove old spike elements
      if (this._spikeEls) {
        for (const el of this._spikeEls) el.remove();
      }
      this._spikeEls = [];
      this._spikeDoses = [];

      const rect = this._plotEl.getBoundingClientRect();
      const plotLeft = margin.l;
      const plotRight = rect.width - margin.r;
      const plotTop = margin.t;
      const plotBottom = rect.height - margin.b;
      const plotW = plotRight - plotLeft;
      const plotH = plotBottom - plotTop;

      // Pre-compute spike data for each visible merged dose
      for (const dose of displayDoses) {
        if (dose.timestamp < tMin || dose.timestamp > tMax) continue;

        // X pixel position
        const xFrac = (dose.timestamp - tMin) / (tMax - tMin);
        const xPx = plotLeft + xFrac * plotW;

        // Compute E2 at this dose's time (sum of all contributions)
        let e2raw = 0;
        for (const d of allDoses) {
          const tDays = (dose.timestamp - d.timestamp) / 86400;
          e2raw += computeE2(tDays, d.dose_mg, d.model, pkParams, patchWearDays);
        }
        let e2 = e2raw * scalingFactor * cf;
        if (baselineE2 > 0 && dose.timestamp >= baselineTestTs) {
          const ageDays = (dose.timestamp - baselineTestTs) / 86400;
          e2 += baselineE2 * Math.exp(-0.02 * Math.max(0, ageDays)) * cf;
        }
        e2 = Math.max(0, e2);

        // Y pixel (invert: 0 at bottom, yMax at top)
        const yFrac = Math.min(e2 / yMax, 1);
        const yPx = plotBottom - yFrac * plotH;

        // Dose label (dosage, ester, time only)
        const modelParts = (dose.model || '').split(' ');
        const esterName = (esters || {})[modelParts[0]] || dose.model;
        const doseLabel = `${dose.dose_mg}mg ${esterName}`;
        const dt = new Date(dose.timestamp * 1000);
        const dateStr = dt.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
        const timeStr = dt.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });

        // Create spike line element (bottom of plot → trace)
        const spike = document.createElement('div');
        spike.className = 'dose-spike';
        spike.style.left = xPx + 'px';
        spike.style.top = yPx + 'px';
        spike.style.height = (plotBottom - yPx) + 'px';
        this._plotEl.appendChild(spike);
        this._spikeEls.push(spike);

        // Create label element
        const label = document.createElement('div');
        label.className = 'dose-spike-label';
        label.textContent = `${doseLabel}\n${dateStr} ${timeStr}`;
        label.style.left = xPx + 'px';
        label.style.top = (yPx - 4) + 'px';
        this._plotEl.appendChild(label);
        this._spikeEls.push(label);

        this._spikeDoses.push({ xPx, spike, label });
      }

      // Store plot bounds for mousemove
      this._spikeBounds = { plotLeft, plotRight, plotTop, plotBottom };

      // Bind events (re-bind if plotEl changed, e.g. after reconnect)
      if (!this._spikeBound || this._spikeTarget !== this._plotEl) {
        this._spikeBound = true;
        this._spikeTarget = this._plotEl;
        this._plotEl.addEventListener('mousemove', (e) => this._onSpikeMove(e));
        this._plotEl.addEventListener('mouseleave', () => this._onSpikeLeave());
      }
    }

    _onSpikeMove(e) {
      if (!this._spikeDoses || !this._spikeBounds) return;
      const rect = this._plotEl.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const b = this._spikeBounds;
      const PROXIMITY = 25; // pixels

      if (x < b.plotLeft || x > b.plotRight) {
        this._onSpikeLeave();
        return;
      }

      // Find the single closest dose within proximity
      let closest = null, closestDist = Infinity;
      for (const s of this._spikeDoses) {
        const dist = Math.abs(x - s.xPx);
        if (dist < PROXIMITY && dist < closestDist) {
          closest = s;
          closestDist = dist;
        }
      }

      for (const s of this._spikeDoses) {
        const show = s === closest;
        s.spike.classList.toggle('visible', show);
        s.label.classList.toggle('visible', show);
      }
    }

    _onSpikeLeave() {
      if (!this._spikeDoses) return;
      for (const s of this._spikeDoses) {
        s.spike.classList.remove('visible');
        s.label.classList.remove('visible');
      }
    }

    disconnectedCallback() {
      if (this._resizeObserver) {
        this._resizeObserver.disconnect();
        this._resizeObserver = null;
      }
      if (this._plotEl && window.Plotly) {
        window.Plotly.purge(this._plotEl);
      }
      this._spikeBound = false;
      this._spikeTarget = null;
    }

    connectedCallback() {
      this._lastEntityKey = null;
      if (this._hass && this.config) this._update();
    }

    getCardSize() {
      return 5;
    }
  }

  customElements.define('estrannaise-card', EstrannaisCard);
}

// ── Card Editor ─────────────────────────────────────────────────────────────

if (!customElements.get('estrannaise-card-editor')) {

  class EstrannaisCardEditor extends HTMLElement {

    setConfig(config) {
      this.config = { ...config };
      // Backward compat: convert legacy hours_* to days_*
      if ('hours_to_show' in config && !('days_to_show' in config)) {
        this.config.days_to_show = Math.max(1, Math.round(config.hours_to_show / 24));
      }
      if ('hours_to_predict' in config && !('days_to_predict' in config)) {
        this.config.days_to_predict = Math.max(1, Math.round(config.hours_to_predict / 24));
      }
      // Only rebuild the form on initial load; skip when the change
      // originated from the form itself (avoids stealing input focus).
      if (this._ignoreNextConfig) {
        this._ignoreNextConfig = false;
        return;
      }
      if (!this._form) {
        this._buildForm();
      } else {
        this._form.data = this._getFormData();
      }
    }

    set hass(hass) {
      this._hass = hass;
      if (this._form) this._form.hass = hass;
    }

    _getFormData() {
      return {
        entity: this.config.entity || '',
        title: this.config.title || 'Estradiol Level',
        icon: this.config.icon || 'mdi:chart-bell-curve-cumulative',
        days_to_show: this.config.days_to_show || 30,
        days_to_predict: this.config.days_to_predict || 7,
        show_target_range: this.config.show_target_range !== false,
        show_danger_threshold: !!this.config.show_danger_threshold,
        show_menstrual_cycle: !!this.config.show_menstrual_cycle,
        show_dose_chevrons: this.config.show_dose_chevrons !== false,
        line_color: hexToRgb(this.config.line_color || '#E91E63'),
        prediction_color: hexToRgb(this.config.prediction_color || this.config.line_color || '#E91E63'),
      };
    }

    _buildForm() {
      if (!this.shadowRoot) this.attachShadow({ mode: 'open' });
      this.shadowRoot.innerHTML = '';

      const form = document.createElement('ha-form');
      form.hass = this._hass;
      form.data = this._getFormData();
      form.schema = [
        { name: 'entity', selector: { entity: { domain: 'sensor' } } },
        { name: 'title', label: 'Title', selector: { text: {} } },
        { name: 'icon', label: 'Icon', selector: { icon: {} } },
        { name: 'days_to_show', label: 'Days to show (past)', selector: { number: { min: 1, max: 365, mode: 'box' } } },
        { name: 'days_to_predict', label: 'Days to predict', selector: { number: { min: 1, max: 365, mode: 'box' } } },
        { name: 'show_target_range', label: 'Show target range', selector: { boolean: {} } },
        { name: 'show_danger_threshold', label: 'Show danger threshold (>500 pg/mL)', selector: { boolean: {} } },
        { name: 'show_menstrual_cycle', label: 'Show menstrual cycle overlay', selector: { boolean: {} } },
        { name: 'show_dose_chevrons', label: 'Show dose markers', selector: { boolean: {} } },
        { name: 'line_color', label: 'Line color', selector: { color_rgb: {} } },
        { name: 'prediction_color', label: 'Prediction color', selector: { color_rgb: {} } },
      ];
      form.computeLabel = (schema) => schema.label || schema.name;

      form.addEventListener('value-changed', (ev) => {
        const data = ev.detail.value;
        const newConfig = { ...this.config };
        // Remove legacy hours_* keys
        delete newConfig.hours_to_show;
        delete newConfig.hours_to_predict;
        for (const [key, val] of Object.entries(data)) {
          if (key === 'line_color' || key === 'prediction_color') {
            newConfig[key] = rgbToHex(val);
          } else {
            newConfig[key] = val;
          }
        }
        // Suppress the setConfig echo so the form keeps focus
        this._ignoreNextConfig = true;
        this.config = newConfig;
        this.dispatchEvent(new CustomEvent('config-changed', {
          bubbles: true, composed: true,
          detail: { config: this.config },
        }));
      });

      this.shadowRoot.appendChild(form);
      this._form = form;
    }
  }

  customElements.define('estrannaise-card-editor', EstrannaisCardEditor);
}

// ── Register with HA ────────────────────────────────────────────────────────

if (!window.customCards) window.customCards = [];
if (!window.customCards.some(c => c.type === 'estrannaise-card')) {
  window.customCards.push({
    type: 'estrannaise-card',
    name: 'Estrannaise HRT Monitor',
    description: 'Estimated blood estradiol levels with pharmacokinetic modeling',
  });
}
