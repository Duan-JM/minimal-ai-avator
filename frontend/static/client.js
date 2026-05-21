var pc = null;
var dc = null; // 数据通道

function negotiate() {
    pc.addTransceiver('video', { direction: 'recvonly' });
    pc.addTransceiver('audio', { direction: 'recvonly' });
    return pc.createOffer().then((offer) => {
        return pc.setLocalDescription(offer);
    }).then(() => {
        // wait for ICE gathering to complete (with 2s timeout)
        return new Promise((resolve) => {
            if (pc.iceGatheringState === 'complete') {
                resolve();
            } else {
                const timeout = setTimeout(() => {
                    pc.removeEventListener('icegatheringstatechange', checkState);
                    console.warn('ICE gathering timeout, proceeding with available candidates');
                    resolve();
                }, 2000);

                const checkState = () => {
                    if (pc.iceGatheringState === 'complete') {
                        clearTimeout(timeout);
                        pc.removeEventListener('icegatheringstatechange', checkState);
                        resolve();
                    }
                };
                pc.addEventListener('icegatheringstatechange', checkState);
            }
        });
    }).then(() => {
        var offer = pc.localDescription;
        return fetch(window.apiUrl('/offer'), {
            body: JSON.stringify({
                sdp: offer.sdp,
                type: offer.type,
            }),
            headers: {
                'Content-Type': 'application/json'
            },
            method: 'POST'
        });
    }).then((response) => {
        return response.json();
    }).then((answer) => {
        document.getElementById('sessionid').value = answer.sessionid
        return pc.setRemoteDescription(answer);
    }).catch((e) => {
        alert(e);
    });
}

function start() {
    var config = {
        sdpSemantics: 'unified-plan',
        iceServers: (window.APP_CONFIG && window.APP_CONFIG.iceServers) || []
    };

    pc = new RTCPeerConnection(config);

    // 创建数据通道
    dc = pc.createDataChannel('chat');
    
    // 数据通道事件处理
    dc.onopen = () => {
        console.log('数据通道已打开');
    };
    
    dc.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleDataChannelMessage(data);
        } catch (error) {
            console.error('处理数据通道消息失败:', error);
        }
    };
    
    dc.onerror = (error) => {
        console.error('数据通道错误:', error);
    };
    
    dc.onclose = () => {
        console.log('数据通道已关闭');
    };

    // connect audio / video
    pc.addEventListener('track', (evt) => {
        if (evt.track.kind == 'video') {
            const videoElement = document.getElementById('video');
            if (videoElement) {
                videoElement.srcObject = evt.streams[0];
            } else {
                console.error('Video element not found');
            }
        } else {
            const audioElement = document.getElementById('audio');
            if (audioElement) {
                audioElement.srcObject = evt.streams[0];
            } else {
                console.error('Audio element not found');
            }
        }
    });

    document.getElementById('start').style.display = 'none';
    negotiate();
    document.getElementById('stop').style.display = 'inline-block';
}

// 处理数据通道消息
function handleDataChannelMessage(data) {
    console.log('收到数据通道消息:', data);
    
    switch (data.type) {
        case 'asr':
            // 语音识别结果
            console.log('ASR结果:', data.text);
            if (typeof addChatMessage === 'function') {
                addChatMessage(data.text, 'user');
            }
            break;
            
        case 'llm':
            // LLM回答 - 添加到聊天窗口
            console.log('LLM回答:', data.text);
            if (typeof addChatMessage === 'function') {
                addChatMessage(data.text, 'assistant');
            }
            break;
            
        case 'tts_start':
            console.log('数字人开始说话');
            break;
            
        case 'tts_end':
            console.log('数字人结束说话');
            break;
            
        case 'error':
            console.error('错误:', data.message);
            break;
    }
}

function stop() {
    document.getElementById('stop').style.display = 'none';

    // 关闭数据通道
    if (dc) {
        dc.close();
        dc = null;
    }

    // close peer connection
    setTimeout(() => {
        pc.close();
    }, 500);
}

window.onunload = function(event) {
    // 在这里执行你想要的操作
    setTimeout(() => {
        pc.close();
    }, 500);
};

window.onbeforeunload = function (e) {
        setTimeout(() => {
                pc.close();
            }, 500);
        e = e || window.event
        // 兼容IE8和Firefox 4之前的版本
        if (e) {
          e.returnValue = '关闭提示'
        }
        // Chrome, Safari, Firefox 4+, Opera 12+ , IE 9+
        return '关闭提示'
      }