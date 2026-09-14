import { LitElement, css, html, nothing } from "lit";
import { customElement, property, state } from "lit/decorators.js";

interface Status {
  status?: string;
  vitrine_age_s?: number;
  cost_today?: number;
  queue_size?: number;
  runtime_version?: string;
  last_update_success?: boolean;
}

interface QueueItem {
  id: string;
  title: string;
  summary?: string;
}

@customElement("pilot-panel")
export class PilotPanel extends LitElement {
  @property({ attribute: false }) public hass!: any;

  @state() private _status: Status = {};
  @state() private _queue: QueueItem[] = [];
  @state() private _vitrine: { fresh?: boolean; lines?: string[]; error?: string } = {};
  @state() private _loading = true;

  static styles = css`
    :host {
      display: block;
      padding: 16px;
      max-width: 860px;
      margin: 0 auto;
      color: var(--primary-text-color);
    }
    h1 { font-size: 1.4em; margin: 0 0 16px; }
    h2 { font-size: 1em; margin: 24px 0 8px; color: var(--secondary-text-color); }
    .card {
      background: var(--card-background-color);
      border-radius: 8px;
      padding: 16px;
      margin-bottom: 8px;
    }
    .row { display: flex; gap: 16px; flex-wrap: wrap; }
    .metric { flex: 1; min-width: 120px; }
    .metric .value { font-size: 1.6em; }
    .metric .label { color: var(--secondary-text-color); font-size: 0.85em; }
    .ok { color: var(--success-color); }
    .bad { color: var(--error-color); }
    button {
      border: none;
      border-radius: 4px;
      padding: 8px 16px;
      cursor: pointer;
      font: inherit;
    }
    .yes { background: var(--success-color); color: #fff; margin-right: 8px; }
    .no { background: var(--error-color); color: #fff; }
    pre {
      white-space: pre-wrap;
      font-family: var(--code-font-family, monospace);
      font-size: 0.85em;
      margin: 0;
    }
    .empty { color: var(--secondary-text-color); }
  `;

  connectedCallback() {
    super.connectedCallback();
    void this._refresh();
    this._timer = window.setInterval(() => void this._refresh(), 30000);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    window.clearInterval(this._timer);
  }

  private _timer!: number;

  private async _refresh() {
    try {
      const [status, queue, vitrine] = await Promise.all([
        this.hass.callWS({ type: "pilot/status" }),
        this.hass.callWS({ type: "pilot/queue" }),
        this.hass.callWS({ type: "pilot/vitrine" }),
      ]);
      this._status = status;
      this._queue = queue.items ?? [];
      this._vitrine = vitrine;
    } finally {
      this._loading = false;
    }
  }

  private async _confirm(item: QueueItem, decision: "yes" | "no") {
    await this.hass.callWS({
      type: "pilot/confirm",
      item_id: item.id,
      decision,
    });
    await this._refresh();
  }

  render() {
    if (this._loading) return html`<p class="empty">Loading…</p>`;
    const s = this._status;
    const fresh =
      s.last_update_success !== false &&
      (s.vitrine_age_s == null || s.vitrine_age_s <= 60);
    return html`
      <h1>Pilot</h1>

      <div class="card row">
        <div class="metric">
          <div class="value ${fresh ? "ok" : "bad"}">${s.status ?? "?"}</div>
          <div class="label">runtime ${s.runtime_version ?? ""}</div>
        </div>
        <div class="metric">
          <div class="value">${s.cost_today ?? "—"}</div>
          <div class="label">cost today (₽)</div>
        </div>
        <div class="metric">
          <div class="value">${s.queue_size ?? 0}</div>
          <div class="label">awaiting decision</div>
        </div>
        <div class="metric">
          <div class="value ${fresh ? "ok" : "bad"}">${fresh ? "fresh" : "stale"}</div>
          <div class="label">home data vitrine</div>
        </div>
      </div>

      <h2>Confirmation queue</h2>
      ${this._queue.length === 0
        ? html`<div class="card empty">Nothing awaits your decision.</div>`
        : this._queue.map(
            (item) => html`
              <div class="card">
                <div><strong>${item.title}</strong></div>
                ${item.summary ? html`<div class="empty">${item.summary}</div>` : nothing}
                <div style="margin-top: 8px">
                  <button class="yes" @click=${() => this._confirm(item, "yes")}>Yes</button>
                  <button class="no" @click=${() => this._confirm(item, "no")}>No</button>
                </div>
              </div>
            `
          )}

      <h2>Home data vitrine</h2>
      <div class="card">
        ${this._vitrine.error
          ? html`<span class="bad">Vitrine unavailable — ${this._vitrine.error}</span>`
          : html`<pre>${(this._vitrine.lines ?? []).join("\n")}</pre>`}
      </div>
    `;
  }
}
