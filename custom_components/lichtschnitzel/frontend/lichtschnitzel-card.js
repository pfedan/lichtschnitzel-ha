// Lichtschnitzel card: one or more screens of a lamp with on/off, brightness, effect and the
// parameters of the effect each screen shows. Served and loaded by the integration itself.
//
//   type: custom:lichtschnitzel-card
//   entity: light.lampe_screen_0        # one screen
//   entities: [light.a, light.b]        # or several
//   title: Wohnzimmer                   # optional
//
// The parameters come from the light's "parameters" attribute; writes go through the services
// light.turn_on/turn_off and lichtschnitzel.set_parameter.

const TEXT = {
  en: { brightness: 'Brightness', effect: 'Effect', off: 'The screen is off.', none: 'This effect has no parameters.', missing: 'Entity not found', unavailable: 'Unavailable', needEntity: 'Set "entity" or "entities" to screens of a Lichtschnitzel lamp.' },
  de: { brightness: 'Helligkeit', effect: 'Effekt', off: 'Der Screen ist aus.', none: 'Dieser Effekt hat keine Parameter.', missing: 'Entität nicht gefunden', unavailable: 'Nicht verfügbar', needEntity: '"entity" oder "entities" auf Screens einer Lichtschnitzel-Lampe setzen.' },
};

const STYLE = `
  ha-card { padding: 12px 16px 16px; }
  .title { font-size: 1.25em; font-weight: 500; margin: 4px 0 8px; }
  .screen + .screen { border-top: 1px solid var(--divider-color); margin-top: 12px; padding-top: 12px; }
  .head { display: flex; align-items: center; gap: 12px; min-height: 40px; }
  .head .name { flex: 1; font-weight: 500; font-size: 1.05em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row { display: grid; grid-template-columns: minmax(6em, 38%) 1fr 4.2em; align-items: center; gap: 8px; min-height: 36px; }
  .row label { color: var(--secondary-text-color); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row .val { text-align: right; font-variant-numeric: tabular-nums; color: var(--secondary-text-color); }
  .row.wide { grid-template-columns: minmax(6em, 38%) 1fr; }
  input[type=range] { width: 100%; accent-color: var(--primary-color); margin: 0; }
  input[type=color] { width: 56px; height: 30px; border: 1px solid var(--divider-color); border-radius: 6px; background: none; padding: 2px; cursor: pointer; }
  select { width: 100%; font: inherit; color: var(--primary-text-color); background: var(--card-background-color, var(--ha-card-background));
           border: 1px solid var(--divider-color); border-radius: 6px; padding: 6px 8px; }
  .params { margin-top: 4px; }
  .note { color: var(--secondary-text-color); font-size: 0.9em; padding: 6px 0; }
  .switch { position: relative; width: 40px; height: 22px; flex: none; }
  .switch input { opacity: 0; width: 0; height: 0; }
  .switch span { position: absolute; inset: 0; border-radius: 11px; background: var(--disabled-color, #999); transition: background .15s; cursor: pointer; }
  .switch span::before { content: ''; position: absolute; width: 16px; height: 16px; left: 3px; top: 3px; border-radius: 50%; background: #fff; transition: transform .15s; }
  .switch input:checked + span { background: var(--primary-color); }
  .switch input:checked + span::before { transform: translateX(18px); }
  .switch input:disabled + span { opacity: .5; cursor: default; }
`;

const hex = rgb => '#' + (rgb || [0, 0, 0]).map(c => Math.max(0, Math.min(255, c | 0)).toString(16).padStart(2, '0')).join('');
const unhex = h => [1, 3, 5].map(n => parseInt(h.slice(n, n + 2), 16));
const label = key => key.split('/').join(' › ');

class LichtschnitzelCard extends HTMLElement {
  setConfig(config) {
    const list = config.entities || (config.entity ? [config.entity] : []);
    if (!list.length) throw new Error(TEXT.en.needEntity);
    this._config = { ...config, entities: list.map(e => (typeof e === 'string' ? e : e.entity)) };
    this._shape = null;
    if (!this.shadowRoot) this.attachShadow({ mode: 'open' });
    if (this._hass) this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._config) this._render();
  }

  getCardSize() {
    return 2 + this._config.entities.length * 4;
  }

  static getStubConfig(hass) {
    const screen = Object.keys(hass.states).find(id => id.startsWith('light.') && Array.isArray(hass.states[id].attributes.parameters));
    return { entity: screen || 'light.lichtschnitzel_screen_0' };
  }

  _t(key) {
    const lang = (this._hass.locale && this._hass.locale.language) || this._hass.language || 'en';
    return (TEXT[lang.split('-')[0]] || TEXT.en)[key];
  }

  // The structure (screens, their effect and parameter keys) is built anew only when it changes;
  // otherwise only the values are updated, and never those of an input in use.
  _render() {
    const states = this._config.entities.map(id => this._hass.states[id]);
    const shape = JSON.stringify([this._hass.language, states.map((s, i) => s
      ? [s.state, s.attributes.friendly_name, s.attributes.effect, (s.attributes.effect_list || []).length,
         'brightness' in s.attributes || (s.attributes.supported_color_modes || []).includes('brightness'),
         (s.attributes.parameters || []).map(p => [p.key, p.type, p.min, p.max])]
      : this._config.entities[i])]);
    if (shape !== this._shape) {
      this._shape = shape;
      this._build(states);
    }
    this._update(states);
  }

  _build(states) {
    const root = this.shadowRoot;
    root.innerHTML = `<style>${STYLE}</style>`;
    const card = document.createElement('ha-card');
    if (this._config.title) card.insertAdjacentHTML('beforeend', `<div class="title"></div>`), (card.lastChild.textContent = this._config.title);
    this._rows = [];
    states.forEach((s, i) => {
      const id = this._config.entities[i];
      const box = document.createElement('div');
      box.className = 'screen';
      card.appendChild(box);
      if (!s) {
        box.innerHTML = `<div class="note"></div>`;
        box.firstChild.textContent = `${this._t('missing')}: ${id}`;
        return;
      }
      const head = document.createElement('div');
      head.className = 'head';
      head.innerHTML = `<div class="name"></div><label class="switch"><input type="checkbox"><span></span></label>`;
      head.querySelector('.name').textContent = s.attributes.friendly_name || id;
      head.querySelector('.name').addEventListener('click', () => this._moreInfo(id));
      const sw = head.querySelector('input');
      sw.addEventListener('change', () => this._call('light', sw.checked ? 'turn_on' : 'turn_off', {}, id));
      box.appendChild(head);
      this._rows.push({ id, el: sw, get: st => st.state === 'on', set: (el, v) => (el.checked = v), dis: true });

      if (s.state === 'unavailable') {
        box.insertAdjacentHTML('beforeend', `<div class="note">${this._t('unavailable')}</div>`);
        return;
      }
      if ((s.attributes.supported_color_modes || []).includes('brightness')) {
        const fmt = v => `${Math.round(v / 2.55)} %`;
        const r = this._slider(this._t('brightness'), 1, 255, 1, fmt, v => this._call('light', 'turn_on', { brightness: v }, id));
        box.appendChild(r.row);
        this._rows.push({ id, el: r.input, out: r.val, get: st => st.attributes.brightness || 0, set: (el, v) => (el.value = v), fmt });
      }
      const list = s.attributes.effect_list || [];
      if (list.length) {
        const row = document.createElement('div');
        row.className = 'row wide';
        row.innerHTML = `<label></label><select></select>`;
        row.firstChild.textContent = this._t('effect');
        const sel = row.querySelector('select');
        for (const name of list) sel.add(new Option(name, name));
        sel.addEventListener('change', () => this._call('light', 'turn_on', { effect: sel.value }, id));
        box.appendChild(row);
        this._rows.push({ id, el: sel, get: st => st.attributes.effect || '', set: (el, v) => (el.value = v) });
      }
      const params = document.createElement('div');
      params.className = 'params';
      box.appendChild(params);
      if (s.state !== 'on') {
        params.innerHTML = `<div class="note">${this._t('off')}</div>`;
        return;
      }
      const ps = s.attributes.parameters || [];
      if (!ps.length) params.innerHTML = `<div class="note">${this._t('none')}</div>`;
      for (const p of ps) {
        const send = v => this._call('lichtschnitzel', 'set_parameter', { parameter: p.key, value: v }, id);
        const find = st => (st.attributes.parameters || []).find(q => q.key === p.key);
        if (p.type === 'Rgb') {
          const row = document.createElement('div');
          row.className = 'row wide';
          row.innerHTML = `<label></label><div><input type="color"></div>`;
          row.firstChild.textContent = row.firstChild.title = label(p.key);
          const input = row.querySelector('input');
          input.addEventListener('input', this._throttle(() => send(unhex(input.value))));
          input.addEventListener('change', () => send(unhex(input.value)));
          params.appendChild(row);
          this._rows.push({ id, el: input, get: st => hex((find(st) || {}).value), set: (el, v) => (el.value = v) });
        } else {
          const int = p.type === 'uint32';
          const step = int ? 1 : (p.max - p.min) / 200 || 0.01;
          const fmt = v => (int ? String(v) : (+v).toFixed(Math.abs(p.max - p.min) >= 20 ? 1 : 2));
          const r = this._slider(label(p.key), p.min, p.max, step, fmt, send);
          params.appendChild(r.row);
          this._rows.push({ id, el: r.input, out: r.val, get: st => (find(st) || {}).value ?? p.min, set: (el, v) => (el.value = v), fmt });
        }
      }
    });
    root.appendChild(card);
  }

  _slider(text, min, max, step, fmt, send) {
    const row = document.createElement('div');
    row.className = 'row';
    row.innerHTML = `<label></label><input type="range"><span class="val"></span>`;
    row.firstChild.textContent = row.firstChild.title = text;
    const input = row.querySelector('input'), val = row.querySelector('.val');
    Object.assign(input, { min, max, step });
    const value = () => (step >= 1 ? Math.round(+input.value) : +input.value);
    input.addEventListener('input', () => { val.textContent = fmt(value()); });
    input.addEventListener('input', this._throttle(() => send(value())));
    input.addEventListener('change', () => send(value()));
    // While the pointer is down the lamp's echo must not move the slider back.
    input.addEventListener('pointerdown', () => (input._busy = true));
    input.addEventListener('pointerup', () => setTimeout(() => (input._busy = false), 600));
    return { row, input, val };
  }

  _update(states) {
    for (const r of this._rows || []) {
      const st = this._hass.states[r.id];
      if (!st) continue;
      if (r.dis) r.el.disabled = st.state === 'unavailable';
      // Not while the slider is held or the colour picker is open: the echo would jump back.
      if (r.el._busy || (r.el.type === 'color' && r.el.matches(':focus'))) continue;
      const v = r.get(st);
      r.set(r.el, v);
      if (r.out) r.out.textContent = r.fmt(v);
    }
  }

  // Sends at most every 150 ms while a slider is dragged; "change" sends the final value.
  _throttle(fn) {
    let timer = null;
    return () => {
      if (timer) return;
      timer = setTimeout(() => { timer = null; fn(); }, 150);
    };
  }

  _call(domain, service, data, entity) {
    this._hass.callService(domain, service, data, { entity_id: entity }).catch(err => {
      this.dispatchEvent(new CustomEvent('hass-notification', { detail: { message: err.message || String(err) }, bubbles: true, composed: true }));
    });
  }

  _moreInfo(entityId) {
    this.dispatchEvent(new CustomEvent('hass-more-info', { detail: { entityId }, bubbles: true, composed: true }));
  }
}

if (!customElements.get('lichtschnitzel-card')) {
  customElements.define('lichtschnitzel-card', LichtschnitzelCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: 'lichtschnitzel-card',
    name: 'Lichtschnitzel',
    description: 'Screens of a Lichtschnitzel lamp with effect and effect parameters.',
    preview: true,
  });
}
