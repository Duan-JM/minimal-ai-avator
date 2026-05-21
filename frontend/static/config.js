// Runtime configuration for the frontend.
//
// 默认空字符串等价于“同源部署”：浏览器会向当前页面所在的 origin 发起 API
// 和静态资源请求，与一体化部署（backend 同时托管前端）行为一致。
//
// 前后端分离部署时，请把下面两个值改成可被浏览器直接访问的后端地址。
// - apiBaseUrl: 后端 HTTP API 与 WebRTC 信令（/offer、/human 等）的基础 URL。
// - mediaBaseUrl: 后端静态媒体（例如 /data/<avatar>/...）的基础 URL，不设置时回落到 apiBaseUrl。
// - iceServers: 透传给浏览器的 RTCPeerConnection 的 iceServers，可用于配置 STUN/TURN。
//
// 示例：
//   window.APP_CONFIG = {
//       apiBaseUrl: 'https://api.example.com',
//       mediaBaseUrl: 'https://api.example.com',
//       iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
//   };
window.APP_CONFIG = Object.assign(
    {
        apiBaseUrl: '',
        mediaBaseUrl: '',
        iceServers: [],
    },
    window.APP_CONFIG || {}
);
