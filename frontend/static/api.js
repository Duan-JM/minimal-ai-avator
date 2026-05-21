// API/媒体 URL 助手。
// 依赖 config.js 在它之前加载，保证 window.APP_CONFIG 已就绪。
(function () {
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

    function apiFetch(path, options) {
        return fetch(apiUrl(path), options);
    }

    window.apiUrl = apiUrl;
    window.mediaUrl = mediaUrl;
    window.apiFetch = apiFetch;
})();
