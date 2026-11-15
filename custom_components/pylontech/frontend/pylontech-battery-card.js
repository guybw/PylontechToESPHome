/**
 * Pylontech Battery Card - ISA-101 High Performance Design
 *
 * Adapted from "Pylontech Battery Overview Card" v3.0.8 by jtubb
 * https://github.com/jtubb/Pylontech-Battery-Card (MIT, see
 * LICENSE-pylontech-battery-card). The layout, styling and heat maps are his.
 *
 * Changed for the Pylontech (ESPHome bridge) integration:
 *  - finds its entities by itself from the integration's devices, so there is
 *    no entity_prefix to type and renamed entities keep working
 *  - stack figures (state of charge, time to full / empty, balance status)
 *    come from the integration's own sensors
 *  - history opens Home Assistant's own more-info dialog (no mini-graph-card)
 */

const CARD_VERSION = '1.0.0';
const DOMAIN = 'pylontech';
const CARD_TAG = 'pylontech-battery-card';

console.info(
  `%c PYLONTECH-BATTERY-CARD %c v${CARD_VERSION} %c ISA-101 `,
  'color: #333; font-weight: bold; background: #d0d0d0',
  'color: #d0d0d0; font-weight: bold; background: #333',
  'color: #fff; font-weight: bold; background: #0066cc'
);

const escapeHtml = (value) =>
  String(value).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));

class PylontechBatteryCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
  }

  setConfig(config) {
    this.config = {
      title: 'PYLONTECH',
      // optional: the stack device to show, when more than one is set up
      device_id: null,
      // ISA-101 thresholds
      soc_warning: 30,
      soc_alarm: 15,
      temp_warning: 40,
      temp_alarm: 50,
      delta_v_warning: 30,
      delta_v_alarm: 50,
      ...config
    };
    this._lastHtml = null;
    this.render();
    this.updateContent();
  }

  set hass(hass) {
    this._hass = hass;
    this.updateContent();
  }

  render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          /* ISA-101 Color Palette */
          --isa-bg: #d4d4d4;
          --isa-bg-panel: #e8e8e8;
          --isa-bg-dark: #b8b8b8;
          --isa-border: #999999;
          --isa-text: #1a1a1a;
          --isa-text-dim: #666666;

          /* Status Colors - ISA-101 compliant */
          --isa-normal: #808080;
          --isa-abnormal: #0088cc;
          --isa-warning: #cc8800;
          --isa-alarm: #cc0000;

          font-family: 'Segoe UI', 'Arial', sans-serif;
          font-size: 13px;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }

        ha-card {
          background: transparent !important;
          border: none !important;
          box-shadow: none !important;
          border-radius: 0 !important;
          overflow: hidden;
        }

        .hmi-container {
          background: var(--isa-bg);
          border: 1px solid var(--isa-border);
          border-radius: 4px;
          padding: 8px;
        }

        /* Header */
        .hmi-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 6px 10px;
          background: var(--isa-bg-dark);
          border: 1px solid var(--isa-border);
          margin-bottom: 8px;
        }
        .hmi-title {
          font-size: 14px;
          font-weight: 600;
          color: var(--isa-text);
          letter-spacing: 1px;
        }
        .health-indicator {
          display: flex;
          align-items: center;
          gap: 6px;
          padding: 4px 10px;
          border-radius: 0;
          font-size: 11px;
          font-weight: 600;
        }
        .health-indicator.level-0 { background: var(--isa-normal); color: #fff; }
        .health-indicator.level-1 { background: var(--isa-abnormal); color: #fff; }
        .health-indicator.level-2 { background: var(--isa-warning); color: #fff; }
        .health-indicator.level-3 { background: var(--isa-alarm); color: #fff; animation: alarm-flash 1s infinite; }

        @keyframes alarm-flash {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.7; }
        }

        /* System Overview */
        .system-overview {
          display: flex;
          flex-direction: column;
          gap: 6px;
          background: var(--isa-bg-panel);
          border: 1px solid var(--isa-border);
          padding: 10px;
          margin-bottom: 8px;
        }
        .system-stats-row {
          display: flex;
          gap: 6px;
        }
        .system-stat {
          flex: 1;
          background: var(--isa-bg);
          border: 1px solid var(--isa-border);
          padding: 8px;
          text-align: center;
          cursor: pointer;
        }
        .system-stat:hover {
          background: var(--isa-bg-dark);
        }
        .system-label {
          font-size: 10px;
          color: var(--isa-text-dim);
          text-transform: uppercase;
          letter-spacing: 0.5px;
          margin-bottom: 4px;
        }
        .system-value {
          font-size: 18px;
          font-weight: 600;
          color: var(--isa-text);
        }
        .system-unit {
          font-size: 11px;
          color: var(--isa-text-dim);
          margin-left: 2px;
        }

        /* Time Estimate Row */
        .time-estimate-row {
          display: flex;
          justify-content: space-between;
          align-items: center;
          background: var(--isa-bg);
          border: 1px solid var(--isa-border);
          padding: 8px 10px;
        }
        .time-estimate-label {
          font-size: 12px;
          color: var(--isa-text-dim);
        }
        .time-estimate-value {
          font-size: 14px;
          font-weight: 600;
          color: var(--isa-text);
        }

        /* Pack Grid */
        .packs-section {
          background: var(--isa-bg-panel);
          border: 1px solid var(--isa-border);
          padding: 10px;
        }
        .packs-grid {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }

        /* Pack Card */
        .pack-card {
          display: flex;
          flex-direction: column;
          gap: 6px;
          background: var(--isa-bg);
          border: 1px solid var(--isa-border);
          padding: 8px;
          overflow: hidden;
        }
        .pack-card.level-2 {
          border-color: var(--isa-warning);
          border-width: 2px;
        }
        .pack-card.level-3 {
          border-color: var(--isa-alarm);
          border-width: 2px;
        }

        /* Pack Top Row */
        .pack-top-row {
          display: flex;
          align-items: center;
          gap: 8px;
        }

        .pack-header {
          min-width: 55px;
          cursor: pointer;
        }
        .pack-header:hover {
          opacity: 0.8;
        }
        .pack-title {
          font-size: 12px;
          font-weight: 600;
          color: var(--isa-text);
        }
        .pack-alert {
          font-size: 10px;
          color: var(--isa-alarm);
          font-weight: 600;
        }

        /* SOC Bar - Horizontal Analog Style */
        .soc-container {
          flex: 1;
          height: 24px;
          background: #fff;
          border: 2px solid var(--isa-border);
          position: relative;
          cursor: pointer;
        }
        .soc-container:hover {
          border-color: var(--isa-text);
        }
        .soc-fill {
          position: absolute;
          top: 0;
          bottom: 0;
          left: 0;
          transition: width 0.3s ease;
        }
        .soc-fill.level-0 { background: var(--isa-normal); }
        .soc-fill.level-1 { background: var(--isa-abnormal); }
        .soc-fill.level-2 { background: var(--isa-warning); }
        .soc-fill.level-3 { background: var(--isa-alarm); }
        .soc-label {
          position: absolute;
          top: 50%;
          left: 50%;
          transform: translate(-50%, -50%);
          font-size: 11px;
          font-weight: 700;
          color: #fff;
          text-shadow: 1px 1px 2px rgba(0,0,0,0.8), -1px -1px 2px rgba(0,0,0,0.8);
          z-index: 1;
        }

        /* Pack Flow in top row */
        .pack-flow {
          margin-left: auto;
        }

        /* Pack Stats Row */
        .pack-stats {
          display: flex;
          gap: 4px;
          min-width: 0;
          overflow: hidden;
        }
        .pack-stat {
          flex: 1;
          display: flex;
          flex-direction: column;
          align-items: center;
          padding: 4px 6px;
          background: var(--isa-bg-panel);
          border: 1px solid var(--isa-border);
          cursor: pointer;
          min-width: 0;
        }
        .pack-stat:hover {
          background: var(--isa-bg-dark);
        }
        .pack-stat-label {
          font-size: 9px;
          color: var(--isa-text-dim);
          text-transform: uppercase;
        }
        .pack-stat-value {
          font-size: 13px;
          font-weight: 600;
          color: var(--isa-text);
        }
        .pack-stat-value.level-0 { color: var(--isa-text); }
        .pack-stat-value.level-1 { color: var(--isa-abnormal); }
        .pack-stat-value.level-2 { color: var(--isa-warning); }
        .pack-stat-value.level-3 { color: var(--isa-alarm); }

        /* Flow Indicator */
        .flow-indicator {
          display: inline-block;
          padding: 2px 6px;
          border-radius: 0;
          font-size: 10px;
          font-weight: 600;
        }
        .flow-indicator.charging { background: var(--isa-abnormal); color: #fff; }
        .flow-indicator.discharging { background: var(--isa-warning); color: #fff; }
        .flow-indicator.idle { background: var(--isa-normal); color: #fff; }

        /* Modal Overlay */
        .modal-overlay {
          position: fixed;
          top: 0;
          left: 0;
          right: 0;
          bottom: 0;
          background: rgba(0, 0, 0, 0.7);
          display: flex;
          align-items: center;
          justify-content: center;
          z-index: 1000;
        }
        .modal-content {
          background: var(--isa-bg);
          border: 2px solid var(--isa-border);
          border-radius: 0;
          padding: 0;
          max-width: 90%;
          max-height: 90%;
          overflow: auto;
          min-width: 320px;
        }
        .modal-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 10px 12px;
          background: var(--isa-bg-dark);
          border-bottom: 1px solid var(--isa-border);
        }
        .modal-title {
          font-size: 14px;
          font-weight: 600;
          color: var(--isa-text);
        }
        .modal-close {
          cursor: pointer;
          font-size: 20px;
          color: var(--isa-text);
          background: none;
          border: 1px solid var(--isa-border);
          padding: 2px 8px;
          border-radius: 0;
        }
        .modal-close:hover {
          background: var(--isa-alarm);
          color: #fff;
          border-color: var(--isa-alarm);
        }
        .modal-body {
          padding: 12px;
        }
        .modal-back {
          cursor: pointer;
          font-size: 12px;
          color: var(--isa-text);
          background: var(--isa-bg-panel);
          border: 1px solid var(--isa-border);
          padding: 4px 10px;
          border-radius: 0;
        }
        .modal-back:hover {
          background: var(--isa-abnormal);
          color: #fff;
        }

        /* Heatmap Grid */
        .heatmap-grid {
          display: grid;
          grid-template-columns: repeat(2, 1fr);
          gap: 8px;
        }
        .heatmap-grid.four-col {
          grid-template-columns: repeat(4, 1fr);
        }
        .heatmap-cell {
          aspect-ratio: 1;
          border: 1px solid var(--isa-border);
          padding: 8px;
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          cursor: pointer;
        }
        .heatmap-cell:hover {
          border-color: var(--isa-text);
          border-width: 2px;
        }
        .heatmap-cell-number {
          font-size: 10px;
          font-weight: 500;
          color: rgba(255, 255, 255, 0.9);
          margin-bottom: 4px;
          text-shadow: 1px 1px 2px rgba(0,0,0,0.8);
        }
        .heatmap-cell-value {
          font-size: 14px;
          font-weight: 700;
          color: white;
          text-shadow: 1px 1px 2px rgba(0,0,0,0.8);
        }

        /* Heatmap Legend */
        .heatmap-legend {
          display: flex;
          justify-content: space-between;
          padding: 8px 10px;
          background: var(--isa-bg-panel);
          border: 1px solid var(--isa-border);
          margin-top: 10px;
          font-size: 12px;
        }
        .legend-item {
          display: flex;
          gap: 6px;
        }
        .legend-item span {
          color: var(--isa-text-dim);
        }
        .legend-item strong {
          color: var(--isa-text);
        }

        /* Status Grid */
        .status-grid {
          display: grid;
          gap: 8px;
        }
        .status-item {
          background: var(--isa-bg-panel);
          padding: 10px;
          border: 1px solid var(--isa-border);
        }
        .status-item.level-2 {
          border-color: var(--isa-warning);
          background: rgba(204, 136, 0, 0.1);
        }
        .status-item.level-3 {
          border-color: var(--isa-alarm);
          background: rgba(204, 0, 0, 0.1);
        }
        .status-label {
          font-size: 10px;
          color: var(--isa-text-dim);
          text-transform: uppercase;
          margin-bottom: 4px;
        }
        .status-value {
          font-size: 14px;
          font-weight: 600;
          color: var(--isa-text);
        }

        /* History Controls */
        .history-controls {
          display: flex;
          gap: 6px;
          padding: 10px;
          justify-content: center;
          background: var(--isa-bg-panel);
          border: 1px solid var(--isa-border);
          margin-top: 10px;
        }
        .history-zoom {
          cursor: pointer;
          padding: 6px 12px;
          border: 1px solid var(--isa-border);
          background: var(--isa-bg);
          color: var(--isa-text);
          font-size: 12px;
          font-weight: 500;
        }
        .history-zoom:hover {
          background: var(--isa-abnormal);
          color: #fff;
          border-color: var(--isa-abnormal);
        }
        .history-graph-container {
          background: var(--isa-bg-panel);
          border: 1px solid var(--isa-border);
          padding: 10px;
          min-height: 200px;
        }

        /* Footer */
        .hmi-footer {
          font-size: 10px;
          color: var(--isa-text-dim);
          text-align: center;
          padding-top: 8px;
          margin-top: 8px;
          border-top: 1px solid var(--isa-border);
        }

        /* Unavailable */
        .unavailable-text {
          text-align: center;
          color: var(--isa-text-dim);
          padding: 20px;
          font-size: 14px;
        }

        /* Mobile responsive */
        @media (max-width: 600px) {
          .system-overview {
            flex-wrap: wrap;
          }
          .system-stat {
            flex: 1 1 calc(50% - 4px);
            min-width: 80px;
          }
          .pack-stats {
            flex-wrap: wrap;
          }
          .pack-stat {
            flex: 1 1 calc(33% - 4px);
            border-right: none;
            border-bottom: 1px solid var(--isa-border);
          }
          .pack-stat:last-child {
            border-bottom: none;
          }
        }
      </style>
      <ha-card>
        <div class="hmi-container">
          <div class="hmi-header">
            <span class="hmi-title">${this.config.title}</span>
            <div class="health-indicator level-0" id="health-indicator">
              <span id="health-text">NORMAL</span>
            </div>
          </div>
          <div id="content"></div>
          <div id="modal"></div>
        </div>
      </ha-card>
    `;
  }

  // --- finding the integration's entities ---------------------------------

  /**
   * Group this integration's entities by device, keyed by translation key.
   * Returns { stack: {...}, packs: [{ num, name, e: {...}, cells: [...] }] }.
   */
  discover() {
    const entities = this._hass.entities || {};
    const devices = this._hass.devices || {};
    if (this._found && this._foundFrom === entities && this._foundDevices === devices) {
      return this._found;
    }

    const byDevice = {};
    Object.values(entities).forEach((entry) => {
      if (entry.platform !== DOMAIN || !entry.device_id || !entry.translation_key) return;
      const device = (byDevice[entry.device_id] = byDevice[entry.device_id] || { cells: [] });
      const isBinary = entry.entity_id.startsWith('binary_sensor.');
      if (entry.translation_key === 'cell_voltage') {
        device.cells.push(entry.entity_id);
      } else {
        device[(isBinary ? 'bin_' : '') + entry.translation_key] = entry.entity_id;
      }
    });

    const stackIds = Object.keys(byDevice).filter(
      (id) => devices[id] && !devices[id].via_device_id
    );
    const stackId = stackIds.includes(this.config.device_id) ? this.config.device_id : stackIds[0];

    const packs = Object.keys(byDevice)
      .filter((id) => stackId && devices[id] && devices[id].via_device_id === stackId)
      .map((id) => {
        const device = devices[id];
        const ident = (device.identifiers || []).map((pair) => String(pair[1])).join(' ');
        const match = ident.match(/_module_(\d+)/);
        const cells = byDevice[id].cells
          .map((entityId) => {
            const m = entityId.match(/cell_(\d+)_voltage/);
            return { entityId, order: m ? parseInt(m[1]) : null };
          })
          .sort((a, b) =>
            a.order !== null && b.order !== null
              ? a.order - b.order
              : a.entityId.localeCompare(b.entityId, undefined, { numeric: true })
          )
          .map((cell) => cell.entityId);
        return {
          num: match ? parseInt(match[1]) : 0,
          name: device.name_by_user || device.name || '',
          e: byDevice[id],
          cells
        };
      })
      .sort((a, b) => a.num - b.num || a.name.localeCompare(b.name, undefined, { numeric: true }));
    packs.forEach((pack, index) => {
      if (!pack.num) pack.num = index + 1;
    });

    this._found = { stack: stackId ? byDevice[stackId] : null, packs };
    this._foundFrom = entities;
    this._foundDevices = devices;
    return this._found;
  }

  state(entityId) {
    const entity = entityId ? this._hass.states[entityId] : null;
    if (!entity || entity.state === 'unavailable' || entity.state === 'unknown') return null;
    return entity.state;
  }

  number(entityId) {
    const value = parseFloat(this.state(entityId));
    return isNaN(value) ? null : value;
  }

  /** The state as Home Assistant would show it (translated enum states). */
  display(entityId) {
    const entity = entityId ? this._hass.states[entityId] : null;
    if (!entity || entity.state === 'unavailable' || entity.state === 'unknown') return null;
    return this._hass.formatEntityState ? this._hass.formatEntityState(entity) : entity.state;
  }

  // --- the card -----------------------------------------------------------

  updateContent() {
    if (!this._hass || !this.config) return;

    const contentDiv = this.shadowRoot.getElementById('content');
    if (!contentDiv) return;

    const { stack, packs } = this.discover();
    if (!stack) {
      this.setContent(contentDiv, '<div class="unavailable-text">No Pylontech battery found</div>');
      return;
    }

    const packData = packs.map((pack) => this.getPackData(pack));
    const online = packData.filter((data) => data.available);
    const alertCount =
      packData.filter((data) => data.alert).length +
      (online.length === 0 && this.state(stack.bin_problem) === 'on' ? 1 : 0);

    // Stack figures come from the integration; fall back to adding up the modules.
    const sum = (key) => online.reduce((total, data) => total + (data[key] === null ? 0 : data[key]), 0);
    const stackPower = this.number(stack.power);
    const stackCurrent = this.number(stack.current);
    const totalPower = stackPower !== null ? stackPower : sum('power');
    const totalCurrent = stackCurrent !== null ? stackCurrent : sum('current');
    const systemVoltage = this.number(stack.voltage);
    const systemSoc = this.number(stack.soc);
    const remaining = this.number(stack.remaining_capacity);
    const full = this.number(stack.full_charge_capacity);
    const stored = this.number(stack.stored_energy);
    const cycles = this.number(stack.cycle_count);
    const balance = this.display(stack.balance_status);
    const score = this.number(stack.balance_score);

    // Health indicator
    const healthIndicator = this.shadowRoot.getElementById('health-indicator');
    const healthText = this.shadowRoot.getElementById('health-text');
    if (alertCount > 0) {
      healthIndicator.className = 'health-indicator level-3';
      healthText.textContent = `${alertCount} ALERT${alertCount > 1 ? 'S' : ''}`;
    } else {
      healthIndicator.className = 'health-indicator level-0';
      healthText.textContent = 'NORMAL';
    }

    const flowState = this.getFlowState(totalCurrent);

    // Time estimate, from the integration's own sensors (hours)
    const toFull = this.number(stack.time_to_full);
    const toEmpty = this.number(stack.time_to_empty);
    let timeLabel = '';
    let timeValue = '';
    let timeEntity = '';
    if (toFull !== null && toFull > 0) {
      timeLabel = 'Full';
      timeValue = this.formatTime(toFull);
      timeEntity = stack.time_to_full;
    } else if (toEmpty !== null && toEmpty > 0) {
      timeLabel = 'Empty';
      timeValue = this.formatTime(toEmpty);
      timeEntity = stack.time_to_empty;
    }

    const stat = (label, value, unit, entityId) => `
          <div class="system-stat"${entityId ? ` data-entity="${entityId}"` : ''}>
            <div class="system-label">${label}</div>
            <div class="system-value">${value}${unit ? `<span class="system-unit">${unit}</span>` : ''}</div>
          </div>`;

    let html = `
      <div class="system-overview">
        ${timeLabel ? `
        <div class="time-estimate-row" data-entity="${timeEntity}">
          <span class="time-estimate-label">Estimated Time to ${timeLabel}:</span>
          <span class="time-estimate-value">${timeValue}</span>
        </div>
        ` : ''}
        <div class="system-stats-row">
          ${systemSoc !== null ? stat('Charge', Math.round(systemSoc), '%', stack.soc) : ''}
          ${stat('Voltage', systemVoltage !== null ? systemVoltage.toFixed(1) : 'N/A', 'V', stack.voltage)}
          ${stat('Power', Math.round(totalPower), 'W', stack.power)}
          ${stat('Status', `<span class="flow-indicator ${flowState.class}">${flowState.text}</span>`, '', '')}
          ${cycles !== null && cycles > 0 ? stat('Cycles', Math.round(cycles), '', stack.cycle_count) : ''}
          ${balance !== null ? stat('Balance', escapeHtml(balance) + (score !== null ? ` <span class="system-unit">${Math.round(score)}/100</span>` : ''), '', stack.balance_status) : ''}
        </div>
      </div>
    `;

    // Pack cards
    html += '<div class="packs-section"><div class="packs-grid">';

    packs.forEach((pack, index) => {
      const data = packData[index];
      if (!data.available) return;

      const alertLevel = data.alert ? 3 : 0;
      const socLevel = this.getSocLevel(data.soc);
      const tempLevel = data.temp === null ? 0 : this.getTempLevel(data.temp, data.tempUnit);
      const deltaVLevel = data.deltaV === null ? 0 : this.getDeltaVLevel(data.deltaV);
      const packFlow = this.getFlowState(data.current);

      html += `
        <div class="pack-card ${alertLevel > 0 ? 'level-' + alertLevel : ''}">
          <div class="pack-top-row">
            <div class="pack-header" data-pack="${index}">
              <div class="pack-title">BATTERY ${pack.num}</div>
              ${data.alert ? '<div class="pack-alert">ALERT</div>' : ''}
            </div>
            <div class="soc-container" data-entity="${pack.e.soc || ''}">
              <div class="soc-fill level-${socLevel}" style="width: ${data.soc}%"></div>
              <div class="soc-label">${data.soc}%</div>
            </div>
            <div class="pack-flow">
              <span class="flow-indicator ${packFlow.class}">${packFlow.text}</span>
            </div>
          </div>
          <div class="pack-stats">
            <div class="pack-stat" data-pack="${index}" data-type="temp">
              <span class="pack-stat-label">Temp</span>
              <span class="pack-stat-value level-${tempLevel}">${data.temp === null ? 'N/A' : data.temp.toFixed(1) + escapeHtml(data.tempUnit)}</span>
            </div>
            <div class="pack-stat" data-entity="${pack.e.power || ''}">
              <span class="pack-stat-label">Power</span>
              <span class="pack-stat-value">${data.power === null ? 'N/A' : Math.round(data.power) + 'W'}</span>
            </div>
            <div class="pack-stat" data-pack="${index}" data-type="voltage">
              <span class="pack-stat-label">ΔV</span>
              <span class="pack-stat-value level-${deltaVLevel}">${data.deltaV === null ? 'N/A' : Math.round(data.deltaV) + 'mV'}</span>
            </div>
          </div>
        </div>
      `;
    });

    html += '</div></div>';

    if (online.length === 0) {
      html = '<div class="unavailable-text">No battery modules available</div>';
    }

    // Footer
    const footer = [`${online.length} Batter${online.length !== 1 ? 'ies' : 'y'} Online`];
    if (remaining !== null) {
      footer.push(`${remaining.toFixed(1)}${full !== null ? ' / ' + full.toFixed(1) : ''} Ah`);
    }
    if (stored !== null) footer.push(`${stored.toFixed(1)} kWh stored`);
    html += `<div class="hmi-footer">${footer.join(' | ')}</div>`;

    this._packs = packs;
    this.setContent(contentDiv, html);
  }

  /** Only touch the DOM when something changed: hass updates arrive constantly. */
  setContent(contentDiv, html) {
    if (html === this._lastHtml) return;
    this._lastHtml = html;
    contentDiv.innerHTML = html;
    this.addEventListeners();
  }

  addEventListeners() {
    // Anything carrying an entity - history click
    this.shadowRoot
      .querySelectorAll('#content [data-entity]')
      .forEach(el => {
        el.addEventListener('click', () => {
          const entityId = el.getAttribute('data-entity');
          if (entityId) this.showHistory(entityId);
        });
      });

    // Pack headers - status click
    this.shadowRoot.querySelectorAll('.pack-header').forEach(el => {
      el.addEventListener('click', () => {
        this.showPackStatus(this._packs[parseInt(el.getAttribute('data-pack'))]);
      });
    });

    // Pack stats with type - heatmap click
    this.shadowRoot.querySelectorAll('.pack-stat[data-type]').forEach(el => {
      el.addEventListener('click', () => {
        const pack = this._packs[parseInt(el.getAttribute('data-pack'))];
        if (el.getAttribute('data-type') === 'voltage') {
          this.showVoltageHeatMap(pack);
        } else {
          this.showTemperatureHeatMap(pack);
        }
      });
    });
  }

  getPackData(pack) {
    const e = pack.e;
    const voltage = this.number(e.voltage);
    const tempEntity = e.temperature ? this._hass.states[e.temperature] : null;
    const soc = this.number(e.soc);
    return {
      available: voltage !== null,
      voltage,
      current: this.number(e.current),
      soc: soc === null ? 0 : Math.round(soc),
      power: this.number(e.power),
      temp: this.number(e.temperature),
      tempUnit: (tempEntity && tempEntity.attributes.unit_of_measurement) || '°C',
      deltaV: this.number(e.cell_voltage_delta),
      alert: this.hasAlert(pack)
    };
  }

  hasAlert(pack) {
    return ['bin_problem', 'bin_voltage_alarm', 'bin_current_alarm', 'bin_temperature_alarm'].some(
      (key) => this.state(pack.e[key]) === 'on'
    );
  }

  getFlowState(current) {
    const val = parseFloat(current);
    if (isNaN(val) || Math.abs(val) < 0.1) {
      return { class: 'idle', text: 'IDLE' };
    } else if (val > 0) {
      return { class: 'charging', text: 'CHG' };
    } else {
      return { class: 'discharging', text: 'DCHG' };
    }
  }

  getSocLevel(soc) {
    if (soc <= this.config.soc_alarm) return 3;
    if (soc <= this.config.soc_warning) return 2;
    return 0;
  }

  getTempLevel(temp, unit) {
    // Convert to Celsius if in Fahrenheit (thresholds are in Celsius)
    let tempC = temp;
    if (unit && unit.includes('F')) {
      tempC = (temp - 32) * 5 / 9;
    }
    if (tempC >= this.config.temp_alarm) return 3;
    if (tempC >= this.config.temp_warning) return 2;
    return 0;
  }

  getDeltaVLevel(deltaV) {
    if (deltaV >= this.config.delta_v_alarm) return 3;
    if (deltaV >= this.config.delta_v_warning) return 2;
    return 0;
  }

  /** The module's temperature sensors (the console has no per-cell temperatures). */
  getTemperatures(pack) {
    const sensors = [
      { key: 'temperature', label: 'Module' },
      { key: 'cell_temp_min', label: 'Coolest cell' },
      { key: 'cell_temp_max', label: 'Warmest cell' },
      { key: 'mos_temperature', label: 'MOSFET' }
    ];

    const cells = [];
    let unit = '°C';

    sensors.forEach(sensor => {
      const entityId = pack.e[sensor.key];
      const value = this.number(entityId);
      if (value !== null) {
        const attributes = this._hass.states[entityId].attributes;
        if (attributes.unit_of_measurement) unit = attributes.unit_of_measurement;
        cells.push({ label: sensor.label, temperature: value, entityId });
      }
    });

    return { cells, unit };
  }

  getCellVoltages(pack) {
    const cells = [];
    pack.cells.forEach((entityId, index) => {
      const value = this.number(entityId);
      if (value !== null) {
        const entity = this._hass.states[entityId];
        cells.push({
          number: index + 1,
          voltage: value,
          balancing: entity.attributes.balancing === true,
          entityId
        });
      }
    });
    return cells;
  }

  getHeatmapColor(normalized) {
    // ISA-101 compliant gradient: blue-gray (cool/low) -> gray (normal) -> amber-red (hot/high)
    // Uses muted industrial colors rather than bright saturated colors
    if (normalized < 0.5) {
      // Cool to neutral: blue-gray (#6699aa) -> gray (#999999)
      const t = normalized * 2; // 0 to 1
      const r = Math.round(102 + t * 51);   // 102 -> 153
      const g = Math.round(153 + t * 0);    // 153 -> 153
      const b = Math.round(170 - t * 17);   // 170 -> 153
      return `rgb(${r}, ${g}, ${b})`;
    } else {
      // Neutral to warm: gray (#999999) -> amber (#cc8800) -> red (#cc4400)
      const t = (normalized - 0.5) * 2; // 0 to 1
      const r = Math.round(153 + t * 51);   // 153 -> 204
      const g = Math.round(153 - t * 85);   // 153 -> 68
      const b = Math.round(153 - t * 153);  // 153 -> 0
      return `rgb(${r}, ${g}, ${b})`;
    }
  }

  showTemperatureHeatMap(pack) {
    const { cells, unit } = this.getTemperatures(pack);
    if (cells.length === 0) return;

    const temps = cells.map(c => c.temperature);
    const minTemp = Math.min(...temps);
    const maxTemp = Math.max(...temps);
    const range = maxTemp - minTemp || 1;

    let html = '<div class="heatmap-grid">';
    cells.forEach(cell => {
      const normalized = (cell.temperature - minTemp) / range;
      const color = this.getHeatmapColor(normalized);
      html += `
        <div class="heatmap-cell" data-entity="${cell.entityId}" style="background-color: ${color};">
          <div class="heatmap-cell-number">${cell.label}</div>
          <div class="heatmap-cell-value">${cell.temperature.toFixed(1)}${escapeHtml(unit)}</div>
        </div>
      `;
    });
    html += '</div>';

    html += `
      <div class="heatmap-legend">
        <div class="legend-item"><span>Min:</span> <strong>${minTemp.toFixed(1)}${escapeHtml(unit)}</strong></div>
        <div class="legend-item"><span>Max:</span> <strong>${maxTemp.toFixed(1)}${escapeHtml(unit)}</strong></div>
        <div class="legend-item"><span>Range:</span> <strong>${(maxTemp - minTemp).toFixed(1)}${escapeHtml(unit)}</strong></div>
      </div>
    `;

    this.showModal(`Battery ${pack.num} - Temperature`, html);
  }

  showVoltageHeatMap(pack) {
    const cells = this.getCellVoltages(pack);
    if (cells.length === 0) {
      this.showModal(
        `Battery ${pack.num} - Cell Voltage`,
        `<div class="unavailable-text">
          Per-cell voltages are switched off.<br>
          Turn on <strong>Per-cell sensors</strong> in the integration's options
          (Settings → Devices &amp; services → Pylontech → Configure).
        </div>`
      );
      return;
    }

    const voltages = cells.map(c => c.voltage);
    const minV = Math.min(...voltages);
    const maxV = Math.max(...voltages);
    const range = maxV - minV || 0.001;

    let html = '<div class="heatmap-grid four-col">';
    cells.forEach(cell => {
      const normalized = (cell.voltage - minV) / range;
      const color = this.getHeatmapColor(normalized);
      const diffMv = Math.round((cell.voltage - minV) * 1000);
      const display = diffMv === 0 ? `${cell.voltage.toFixed(3)}V` : `+${diffMv}mV`;
      html += `
        <div class="heatmap-cell" data-entity="${cell.entityId}" style="background-color: ${color};">
          <div class="heatmap-cell-number">Cell ${cell.number}${cell.balancing ? ' ⚖' : ''}</div>
          <div class="heatmap-cell-value">${display}</div>
        </div>
      `;
    });
    html += '</div>';

    const deltaVmv = Math.round((maxV - minV) * 1000);
    html += `
      <div class="heatmap-legend">
        <div class="legend-item"><span>Min:</span> <strong>${minV.toFixed(3)}V</strong></div>
        <div class="legend-item"><span>Max:</span> <strong>${maxV.toFixed(3)}V</strong></div>
        <div class="legend-item"><span>ΔV:</span> <strong>${deltaVmv}mV</strong></div>
      </div>
    `;

    this.showModal(`Battery ${pack.num} - Cell Voltage`, html);
  }

  showPackStatus(pack) {
    const rows = [
      { key: 'module_state', label: 'State' },
      { key: 'health', label: 'Cell Health', alertOn: ['degrading', 'failed'] },
      { key: 'bin_problem', label: 'Problem' },
      { key: 'bin_voltage_alarm', label: 'Voltage Alarm' },
      { key: 'bin_current_alarm', label: 'Current Alarm' },
      { key: 'bin_temperature_alarm', label: 'Temperature Alarm' },
      { key: 'bin_balancing', label: 'Balancing', neverAlert: true }
    ];

    let html = '<div class="status-grid">';
    rows.forEach(row => {
      const entityId = pack.e[row.key];
      const state = this.state(entityId);
      if (state === null) return;
      const isBinary = row.key.startsWith('bin_');
      const isAlert = !row.neverAlert && (
        isBinary ? state === 'on' : (row.alertOn || []).includes(state)
      );
      let text = this.display(entityId);
      if (isBinary && !row.neverAlert) text = state === 'on' ? 'ALARM' : 'Normal';
      html += `
        <div class="status-item ${isAlert ? 'level-3' : ''}" data-entity="${entityId}">
          <div class="status-label">${row.label}</div>
          <div class="status-value">${escapeHtml(text)}</div>
        </div>
      `;
    });
    html += '</div>';

    this.showModal(`Battery ${pack.num} - Status`, html);
  }

  showModal(title, bodyHtml) {
    const modalDiv = this.shadowRoot.getElementById('modal');

    modalDiv.innerHTML = `
      <div class="modal-overlay">
        <div class="modal-content">
          <div class="modal-header">
            <div class="modal-title">${escapeHtml(title)}</div>
            <button class="modal-close" id="closeModal">×</button>
          </div>
          <div class="modal-body">${bodyHtml}</div>
        </div>
      </div>
    `;

    // Close handlers
    this.shadowRoot.getElementById('closeModal').addEventListener('click', () => {
      modalDiv.innerHTML = '';
    });
    this.shadowRoot.querySelector('.modal-overlay').addEventListener('click', (e) => {
      if (e.target.classList.contains('modal-overlay')) modalDiv.innerHTML = '';
    });

    // Cell / status clicks open that entity's history
    modalDiv.querySelectorAll('[data-entity]').forEach(cell => {
      cell.addEventListener('click', () => {
        const entityId = cell.getAttribute('data-entity');
        if (entityId) this.showHistory(entityId);
      });
    });
  }

  showHistory(entityId) {
    const event = new Event('hass-more-info', { bubbles: true, composed: true });
    event.detail = { entityId };
    this.dispatchEvent(event);
  }

  formatTime(hours) {
    if (hours < 1) return `${Math.round(hours * 60)}m`;
    if (hours < 24) {
      const h = Math.floor(hours);
      const m = Math.round((hours - h) * 60);
      return m > 0 ? `${h}h ${m}m` : `${h}h`;
    }
    const days = Math.floor(hours / 24);
    const h = Math.round(hours % 24);
    return h > 0 ? `${days}d ${h}h` : `${days}d`;
  }

  getCardSize() {
    return 4 + (this._packs ? this._packs.length : 4);
  }

  static getStubConfig() {
    return {};
  }
}

if (!customElements.get(CARD_TAG)) {
  customElements.define(CARD_TAG, PylontechBatteryCard);

  window.customCards = window.customCards || [];
  window.customCards.push({
    type: CARD_TAG,
    name: 'Pylontech Battery',
    description: 'Battery stack overview with per-battery tiles and cell heat maps',
    preview: true,
    documentationURL: 'https://github.com/guybw/PylontechToESPHome'
  });
}
