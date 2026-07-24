/**
 * API 请求封装 — 统一错误处理、JSON 解析。
 */

const API_BASE = '/api';

async function apiGet(path, params = {}) {
    const query = new URLSearchParams(params).toString();
    const url = query ? `${API_BASE}${path}?${query}` : `${API_BASE}${path}`;
    const resp = await fetch(url);
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({ message: resp.statusText }));
        throw new Error(err.detail || err.message || `HTTP ${resp.status}`);
    }
    const body = await resp.json();
    if (body.code !== 0) throw new Error(body.message || 'Unknown error');
    return body;
}

async function apiPost(path, data = {}) {
    const resp = await fetch(`${API_BASE}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: data ? JSON.stringify(data) : undefined,
    });
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({ message: resp.statusText }));
        throw new Error(err.detail || err.message || `HTTP ${resp.status}`);
    }
    const body = await resp.json();
    if (body.code !== 0) throw new Error(body.message || 'Unknown error');
    return body;
}