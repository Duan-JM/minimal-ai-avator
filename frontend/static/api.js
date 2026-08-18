// API/媒体 URL 助手。
// 依赖 config.js 在它之前加载，保证 window.APP_CONFIG 已就绪。
(function () {
    const DEFAULT_TIMEOUT_MS = 10000;

    class ApiError extends Error {
        constructor(message, { code = 'request_failed', status = 0, retryable = false } = {}) {
            super(message);
            this.name = 'ApiError';
            this.code = code;
            this.status = status;
            this.retryable = retryable;
        }
    }

    function stripTrailingSlash(url) {
        if (typeof url !== 'string') return '';
        return url.replace(/\/+$/, '');
    }

    function isAbsoluteUrl(value) {
        return typeof value === 'string' && /^[a-z][a-z0-9+.-]*:\/\//i.test(value);
    }

    function getApiBaseUrl() {
        const cfg = window.APP_CONFIG || {};
        return stripTrailingSlash(cfg.apiBaseUrl);
    }

    function getMediaBaseUrl() {
        const cfg = window.APP_CONFIG || {};
        const media = stripTrailingSlash(cfg.mediaBaseUrl);
        return media || getApiBaseUrl();
    }

    function joinBase(base, path) {
        if (!base) return path;
        if (!path) return base;
        if (path.startsWith('/')) return base + path;
        return base + '/' + path;
    }

    // 把 `/offer`、`/api/avatars` 等同源路径前缀化为 apiBaseUrl + path。
    // 绝对 URL 原样返回；非以 `/` 开头的相对路径也原样返回，便于嵌入第三方资源。
    function apiUrl(path) {
        if (!path) return path || '';
        if (isAbsoluteUrl(path)) return path;
        if (!path.startsWith('/')) return path;
        return joinBase(getApiBaseUrl(), path);
    }

    // 用于 `<img src>` / CSS background-image 等媒体资源。
    // null/undefined 一律返回空字符串，避免 `src="undefined"`。
    function mediaUrl(path) {
        if (path === null || path === undefined || path === '') return '';
        if (isAbsoluteUrl(path)) return path;
        if (!path.startsWith('/')) return path;
        return joinBase(getMediaBaseUrl(), path);
    }

    async function apiFetch(path, options = {}) {
        const {
            timeoutMs = DEFAULT_TIMEOUT_MS,
            signal: externalSignal,
            ...fetchOptions
        } = options;
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), timeoutMs);

        if (externalSignal) {
            if (externalSignal.aborted) {
                controller.abort();
            } else {
                externalSignal.addEventListener('abort', () => controller.abort(), { once: true });
            }
        }

        try {
            const response = await fetch(apiUrl(path), {
                ...fetchOptions,
                signal: controller.signal,
            });
            if (!response.ok) {
                let payload = {};
                try {
                    payload = await response.clone().json();
                } catch (error) {
                    console.warn('API错误响应不是JSON:', error);
                }
                throw new ApiError(
                    payload.msg || `Request failed with status ${response.status}`,
                    {
                        code: payload.error || 'http_error',
                        status: response.status,
                        retryable: response.status === 429 || response.status >= 500,
                    }
                );
            }
            return response;
        } catch (error) {
            if (error instanceof ApiError) {
                throw error;
            }
            if (error && error.name === 'AbortError') {
                throw new ApiError('请求超时，请重试', {
                    code: 'request_timeout',
                    retryable: true,
                });
            }
            throw new ApiError('无法连接到服务，请检查网络或服务状态', {
                code: 'network_error',
                retryable: true,
            });
        } finally {
            clearTimeout(timeout);
        }
    }

    async function apiJson(path, options) {
        const response = await apiFetch(path, options);
        try {
            return await response.json();
        } catch (error) {
            throw new ApiError('服务返回了无效响应', {
                code: 'invalid_response',
                status: response.status,
            });
        }
    }

    window.ApiError = ApiError;
    window.apiUrl = apiUrl;
    window.mediaUrl = mediaUrl;
    window.apiFetch = apiFetch;
    window.apiJson = apiJson;
})();
