/**
 * AI Avatar WebRTC 客户端 - Talk 页面版本
 */

class AvatarClient {
    constructor() {
        // WebRTC 相关
        this.pc = null;
        this.dataChannel = null;
        this.mediaRecorder = null;
        this.audioChunks = [];
        this.recognition = null;
        this.speechRecognitionSupported = false;
        
        // 状态管理
        this.isConnected = false;
        this.isRecording = false;
        this.isSpeaking = false;
        this.sessionid = 0;
        this.subtitleEnabled = true;  // 字幕开关状态
        this.subtitleTimer = null;  // 字幕隐藏定时器
        this.currentSubtitle = '';  // 当前显示的字幕文本
        
        // DOM 元素
        this.remoteVideo = document.getElementById('remoteVideo');
        this.remoteAudio = document.getElementById('remoteAudio');
        this.loadingOverlay = document.getElementById('loadingOverlay');
        this.subtitleOverlay = document.getElementById('subtitleOverlay');
        this.startChatBtn = document.getElementById('startChatBtn');
        this.retryConnectionBtn = document.getElementById('retryConnectionBtn');
        this.errorBanner = document.getElementById('errorBanner');
        this.errorTimer = null;
        this.connectionRetryTimer = null;
        this.setupRemoteMediaDiagnostics();
        
        // 获取URL参数
        this.avatarId = this.getUrlParam('avatar') || 'ai_model';
        this.avatarName = 'AI Avatar';  // 默认名称，稍后从配置获取
        this.avatarImage = '';  // avatar图片路径
        
        // 初始化
        this.init();
    }

    async init() {
        try {
            // 从API获取avatar配置
            await this.loadAvatarConfig();
            
            // 设置页面标题
            document.title = `与${this.avatarName}对话`;
            
            // 隐藏所有控制按钮（calling状态）
            this.hideControlButtons();
            this.setupStartButton();
            this.setupRetryButton();

            // 预先绑定一次用户交互恢复播放，兼容移动端自动播放策略
            this.bindMediaResumeHandlers();
            
            // 连接WebRTC
            await this.connect();
            
            // 设置语音识别 - 参考index.html
            this.setupSpeechRecognition();
            
            // 设置按住说话 - 参考index.html
            this.setupPushToTalk();
            
        } catch (error) {
            console.error('初始化失败:', error);
            this.showConnectionError(this.formatConnectionError(error));
        }
    }

    getUrlParam(name) {
        const urlParams = new URLSearchParams(window.location.search);
        return urlParams.get(name);
    }

    async loadAvatarConfig() {
        try {
            const result = await window.apiJson('/api/avatars');
            
            if (result.code === 0 && result.data) {
                const avatarConfig = result.data.find(a => a.id === this.avatarId);
                if (avatarConfig) {
                    this.avatarName = avatarConfig.name;
                    this.avatarImage = window.mediaUrl(avatarConfig.image);
                    console.log(`Avatar配置加载成功: ${this.avatarName}, 图片: ${this.avatarImage}`);
                    
                    // 设置加载背景图和图标
                    this.setLoadingBackground();
                } else {
                    console.warn(`未找到avatar配置: ${this.avatarId}`);
                }
            }
        } catch (error) {
            console.error('加载avatar配置失败:', error);
            throw error;
        }
    }

    setLoadingBackground() {
        if (this.avatarImage) {
            // 设置加载遮罩的背景图（使用独立的背景层）
            const loadingBg = document.getElementById('loadingBackground');
            if (loadingBg) {
                loadingBg.style.backgroundImage = `url(${this.avatarImage})`;
            }
            
            // 设置加载图标为avatar头像
            const loadingIcon = document.getElementById('loadingIcon');
            if (loadingIcon) {
                loadingIcon.innerHTML = `<img src="${this.avatarImage}" alt="${this.avatarName}">`;
            }
        }
    }

    updateLoadingProgress(text) {
        const progressEl = document.getElementById('loadingProgress');
        if (progressEl) {
            progressEl.textContent = text;
        }
    }

    bindMediaResumeHandlers() {
        const resume = () => {
            this.resumeRemotePlayback();
        };

        document.addEventListener('click', resume, { passive: true });
        document.addEventListener('touchstart', resume, { passive: true });
        document.addEventListener('keydown', resume);
    }

    setupStartButton() {
        if (!this.startChatBtn) return;
        this.startChatBtn.addEventListener('click', async () => {
            try {
                await this.resumeRemotePlayback();
                this.hideStartButton();
            } catch (error) {
                console.warn('手动开启声音失败:', error);
            }
        });
    }

    setupRetryButton() {
        if (!this.retryConnectionBtn) return;
        this.retryConnectionBtn.addEventListener('click', () => {
            this.retryConnection();
        });
    }

    showStartButton(message = '点击开启声音') {
        if (!this.startChatBtn) return;
        this.startChatBtn.innerHTML = `<i class="bi bi-volume-up-fill"></i>${message}`;
        this.startChatBtn.style.display = 'inline-flex';
    }

    hideStartButton() {
        if (!this.startChatBtn) return;
        this.startChatBtn.style.display = 'none';
    }

    setupRemoteMediaDiagnostics() {
        if (this.remoteAudio) {
            const logAudioEvent = (eventName) => () => {
                console.log('远端音频元素事件', eventName, {
                    paused: this.remoteAudio.paused,
                    muted: this.remoteAudio.muted,
                    volume: this.remoteAudio.volume,
                    readyState: this.remoteAudio.readyState,
                    currentTime: this.remoteAudio.currentTime,
                    hasStream: !!this.remoteAudio.srcObject,
                    error: this.remoteAudio.error ? this.remoteAudio.error.code : null
                });
            };
            this.remoteAudio.addEventListener('loadstart', logAudioEvent('loadstart'));
            this.remoteAudio.addEventListener('loadedmetadata', logAudioEvent('loadedmetadata'));
            this.remoteAudio.addEventListener('canplay', logAudioEvent('canplay'));
            this.remoteAudio.addEventListener('play', logAudioEvent('play'));
            this.remoteAudio.addEventListener('playing', logAudioEvent('playing'));
            this.remoteAudio.addEventListener('pause', logAudioEvent('pause'));
            this.remoteAudio.addEventListener('waiting', logAudioEvent('waiting'));
            this.remoteAudio.addEventListener('stalled', logAudioEvent('stalled'));
            this.remoteAudio.addEventListener('error', logAudioEvent('error'));
            this.remoteAudio.addEventListener('volumechange', logAudioEvent('volumechange'));
        }

        if (this.remoteVideo) {
            ['loadedmetadata', 'canplay', 'play', 'playing', 'error'].forEach((eventName) => {
                this.remoteVideo.addEventListener(eventName, () => {
                    console.log('远端视频元素事件', eventName, {
                        paused: this.remoteVideo.paused,
                        readyState: this.remoteVideo.readyState,
                        videoWidth: this.remoteVideo.videoWidth,
                        videoHeight: this.remoteVideo.videoHeight
                    });
                });
            });
        }
    }

    async resumeRemotePlayback() {
        if (this.remoteVideo && this.remoteVideo.srcObject) {
            try {
                await this.remoteVideo.play();
                console.log('video play() 调用成功', {
                    paused: this.remoteVideo.paused,
                    readyState: this.remoteVideo.readyState
                });
            } catch (error) {
                console.warn('视频自动播放等待用户交互:', error);
                this.showStartButton('点击开启声音');
            }
        }

        if (this.remoteAudio && this.remoteAudio.srcObject) {
            this.remoteAudio.muted = false;
            this.remoteAudio.volume = 1.0;
            try {
                await this.remoteAudio.play();
                console.log('audio play() 调用成功', {
                    paused: this.remoteAudio.paused,
                    muted: this.remoteAudio.muted,
                    volume: this.remoteAudio.volume,
                    readyState: this.remoteAudio.readyState,
                    currentTime: this.remoteAudio.currentTime
                });
                this.hideStartButton();
            } catch (error) {
                console.warn('音频自动播放等待用户交互:', error);
                this.showStartButton('点击开启声音');
            }
        }
    }

    async connect() {
        try {
            // 立即开始negotiate，减少延迟
            // 创建 RTCPeerConnection
            this.pc = new RTCPeerConnection({
                sdpSemantics: 'unified-plan',
                iceServers: (window.APP_CONFIG && window.APP_CONFIG.iceServers) || []
            });

            // 监听远程视频流
            this.pc.addEventListener('track', async (event) => {
                console.log('收到远程媒体流:', event.track.kind);
                const stream = event.streams[0] || new MediaStream([event.track]);
                console.log('远端流详情:', {
                    streamId: stream.id,
                    audioTracks: stream.getAudioTracks().length,
                    videoTracks: stream.getVideoTracks().length,
                    trackEnabled: event.track.enabled,
                    trackMuted: event.track.muted,
                    trackReadyState: event.track.readyState
                });

                event.track.onmute = () => console.warn('远端track mute:', event.track.kind);
                event.track.onunmute = () => console.log('远端track unmute:', event.track.kind);
                event.track.onended = () => console.warn('远端track ended:', event.track.kind);

                if (event.track.kind === 'video') {
                    this.remoteVideo.muted = true;
                    this.remoteVideo.srcObject = stream;
                    // 低延迟播放模式，减少 jitter buffer
                    if ('requestVideoFrameCallback' in HTMLVideoElement.prototype) {
                        console.log('浏览器支持 requestVideoFrameCallback，启用帧级监控');
                        this.setupVideoFrameCallback();
                    }
                    this.hideLoading();
                    // 接通后显示所有控制按钮
                    this.showControlButtons();
                    await this.resumeRemotePlayback();
                    // 启动 WebRTC 连接质量监控
                    this.startStatsMonitor();
                } else if (event.track.kind === 'audio') {
                    this.remoteAudio.srcObject = stream;
                    console.log('远端音频流已绑定到 audio 元素');
                    await this.resumeRemotePlayback();
                }
            });

            // ICE候选处理
            this.pc.onicecandidate = (event) => {
                if (event.candidate) {
                    console.log('ICE候选:', event.candidate);
                }
            };

            // 连接状态监听
            this.pc.onconnectionstatechange = () => {
                console.log('连接状态:', this.pc.connectionState);
                if (this.pc.connectionState === 'connected') {
                    this.isConnected = true;
                    if (this.connectionRetryTimer) {
                        clearTimeout(this.connectionRetryTimer);
                        this.connectionRetryTimer = null;
                    }
                    this.hideLoading();
                    // 接通后显示所有控制按钮
                    this.showControlButtons();
                    this.resumeRemotePlayback();
                } else if (this.pc.connectionState === 'failed') {
                    this.showConnectionError('连接已失败，请重新连接');
                } else if (this.pc.connectionState === 'disconnected') {
                    if (this.connectionRetryTimer) {
                        clearTimeout(this.connectionRetryTimer);
                    }
                    this.connectionRetryTimer = setTimeout(() => {
                        if (this.pc && this.pc.connectionState === 'disconnected') {
                            this.showConnectionError('连接已中断，请重新连接');
                        }
                    }, 3000);
                }
            };

            // 创建数据通道
            this.dataChannel = this.pc.createDataChannel('chat');
            this.setupDataChannel();

            await this.negotiate();

            console.log('WebRTC连接成功');

        } catch (error) {
            console.error('连接失败:', error);
            throw error;
        }
    }

    async negotiate() {
        try {
            // 添加进度提示
            this.updateLoadingProgress('正在建立连接...');
            
            this.pc.addTransceiver('video', { direction: 'recvonly' });
            this.pc.addTransceiver('audio', { direction: 'recvonly' });

            // 创建 offer
            const offer = await this.pc.createOffer();
            await this.pc.setLocalDescription(offer);

            this.updateLoadingProgress('正在收集信息...');
            
            // 等待 ICE gathering 完成，设置超时避免长时间阻塞
            await new Promise((resolve) => {
                if (this.pc.iceGatheringState === 'complete') {
                    resolve();
                } else {
                    // 最多等待 2 秒，避免 ICE 收集耗时过长
                    const timeout = setTimeout(() => {
                        this.pc.removeEventListener('icegatheringstatechange', checkState);
                        console.warn('ICE gathering 超时，使用已有候选继续');
                        resolve();
                    }, 2000);

                    const checkState = () => {
                        if (this.pc.iceGatheringState === 'complete') {
                            clearTimeout(timeout);
                            this.pc.removeEventListener('icegatheringstatechange', checkState);
                            resolve();
                        }
                    };
                    this.pc.addEventListener('icegatheringstatechange', checkState);
                }
            });

            this.updateLoadingProgress('正在加载数字人...');
            
            // 记录开始时间
            const startTime = Date.now();
            
            // 发送 offer 到服务器
            const answer = await window.apiJson('/offer', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    sdp: this.pc.localDescription.sdp,
                    type: this.pc.localDescription.type,
                    avatar_id: this.avatarId
                })
            });
            
            // 记录耗时
            const elapsedTime = Date.now() - startTime;
            console.log(`Offer请求耗时: ${elapsedTime}ms`);
            
            if (elapsedTime > 3000) {
                console.warn('Offer请求耗时过长，可能是因为后端需要加载avatar模型');
            }
            
            this.updateLoadingProgress('正在建立视频连接...');
            
            // 保存sessionid
            this.sessionid = answer.sessionid;
            console.log('Session ID:', this.sessionid);
            
            // 设置远程描述
            await this.pc.setRemoteDescription(answer);
            
            this.updateLoadingProgress('等待视频流...');
        } catch (error) {
            console.error('Negotiate失败:', error);
            this.updateLoadingProgress('连接失败');
            throw error;
        }
    }

    setupDataChannel() {
        this.dataChannel.onopen = () => {
            console.log('数据通道已打开');
        };

        this.dataChannel.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this.handleDataChannelMessage(data);
            } catch (error) {
                console.error('处理消息失败:', error);
            }
        };

        this.dataChannel.onerror = (error) => {
            console.error('数据通道错误:', error);
        };

        this.dataChannel.onclose = () => {
            console.log('数据通道已关闭');
            this.isConnected = false;
        };
    }

    handleDataChannelMessage(data) {
        console.log('收到数据通道消息:', data);

        switch (data.type) {
            case 'asr':
                // 语音识别结果
                console.log('ASR结果:', data.text);
                this.showSubtitle(data.text);
                // 添加到聊天窗口
                this.addChatMessage('user', data.text);
                break;
                
            case 'llm':
                // AI回复 - 显示在字幕和聊天窗口
                console.log('LLM回答:', data.text);
                // 累积字幕文本（因为LLM是流式返回，按句子分段）
                this.currentSubtitle = data.text;
                this.showSubtitle(data.text);
                // 添加到聊天窗口
                this.addChatMessage('assistant', data.text);
                
                // 清除之前的定时器
                if (this.subtitleTimer) {
                    clearTimeout(this.subtitleTimer);
                    this.subtitleTimer = null;
                }
                
                // 注意：不在这里设置隐藏定时器，等待 tts_end 事件
                break;
                
            case 'tts_start':
                // 开始说话
                this.isSpeaking = true;
                console.log('数字人开始说话');
                
                // 清除任何现有的隐藏定时器
                if (this.subtitleTimer) {
                    clearTimeout(this.subtitleTimer);
                    this.subtitleTimer = null;
                }
                break;
                
            case 'tts_end':
                // 结束说话
                this.isSpeaking = false;
                console.log('数字人结束说话');
                
                // 数字人说完话后，延迟3秒再隐藏字幕
                if (this.subtitleTimer) {
                    clearTimeout(this.subtitleTimer);
                }
                this.subtitleTimer = setTimeout(() => {
                    this.hideSubtitle();
                }, 3000);
                break;
                
            case 'error':
                // 错误消息
                console.error('错误:', data.message);
                this.showError(data.message);
                break;
        }
    }

    // 参考index.html的对话模式实现，使用main.py的human函数
    async sendTextMessage(text) {
        if (!text || !text.trim()) return;

        try {
            console.log('发送聊天消息:', text);
            
            // 显示在字幕上
            this.showSubtitle(text);
            
            // 添加到聊天窗口
            this.addChatMessage('user', text);
            
            // 使用/human接口，type='chat' - 参考index.html
            const data = await window.apiJson('/human', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    text: text,
                    type: 'chat',
                    interrupt: true,
                    sessionid: this.sessionid
                })
            });
            console.log('发送成功:', data);
            
            // LLM回答会通过数据通道返回，在handleDataChannelMessage中处理

        } catch (error) {
            console.error('发送消息失败:', error);
            this.showError(this.formatConnectionError(error));
        }
    }

    // 添加聊天消息到窗口
    addChatMessage(role, text) {
        const chatMessages = document.getElementById('chatMessages');
        if (!chatMessages) {
            console.error('chatMessages元素未找到');
            return;
        }

        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${role}`;
        
        const labelDiv = document.createElement('div');
        labelDiv.className = 'message-label';
        labelDiv.textContent = role === 'user' ? '' : this.avatarName;
        
        const bubbleDiv = document.createElement('div');
        bubbleDiv.className = 'message-bubble';
        bubbleDiv.textContent = text;
        
        messageDiv.appendChild(labelDiv);
        messageDiv.appendChild(bubbleDiv);
        chatMessages.appendChild(messageDiv);
        
        // 滚动到底部
        chatMessages.scrollTop = chatMessages.scrollHeight;
        
        console.log('添加聊天消息:', role, text);
    }

    // 参考index.html的语音识别实现
    setupSpeechRecognition() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        
        if (SpeechRecognition) {
            this.speechRecognitionSupported = true;
            this.recognition = new SpeechRecognition();
            this.recognition.continuous = true; // 持续识别
            this.recognition.interimResults = true; // 中间结果
            this.recognition.lang = 'zh-CN';

            this.recognition.onresult = (event) => {
                let interimTranscript = '';
                let finalTranscript = '';
                
                for (let i = event.resultIndex; i < event.results.length; ++i) {
                    if (event.results[i].isFinal) {
                        finalTranscript += event.results[i][0].transcript;
                    } else {
                        interimTranscript += event.results[i][0].transcript;
                    }
                }
                
                // 显示中间结果在字幕上
                if (interimTranscript) {
                    this.showSubtitle(interimTranscript);
                }
                
                // 最终结果发送到服务器
                if (finalTranscript) {
                    console.log('语音识别最终结果:', finalTranscript);
                    this.sendTextMessage(finalTranscript);
                }
            };

            this.recognition.onerror = (event) => {
                console.error('语音识别错误:', event.error);
                if (event.error !== 'no-speech') {
                    this.showError('语音识别失败: ' + event.error);
                }
            };

            this.recognition.onend = () => {
                console.log('语音识别结束');
                this.stopVoiceInput();
            };
        } else {
            console.warn('浏览器不支持语音识别');
            this.speechRecognitionSupported = false;
        }
    }

    // 参考index.html的按住说话功能
    setupPushToTalk() {
        // 在全屏模式下，使用整个屏幕作为按住说话区域
        const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
        let touchStartTime = 0;
        let recordingTimeout;
        
        // 为整个文档添加触摸事件
        document.addEventListener('touchstart', (e) => {
            // 避免在点击按钮时触发
            if (e.target.tagName === 'BUTTON' || e.target.closest('button') || e.target.closest('.chat-window')) {
                return;
            }
            
            touchStartTime = Date.now();
            
            // 延迟启动录音，避免误触
            recordingTimeout = setTimeout(() => {
                this.startVoiceInput();
            }, 200);
        });
        
        document.addEventListener('touchend', (e) => {
            if (e.target.tagName === 'BUTTON' || e.target.closest('button') || e.target.closest('.chat-window')) {
                return;
            }
            
            // 清除延迟启动
            if (recordingTimeout) {
                clearTimeout(recordingTimeout);
            }
            
            // 检查是否是短按（小于200ms）
            const touchDuration = Date.now() - touchStartTime;
            if (touchDuration < 200 && !this.isRecording) {
                return;
            }
            
            if (this.isRecording) {
                this.stopVoiceInput();
            }
        });
        
        // 桌面端：使用空格键
        document.addEventListener('keydown', (e) => {
            if (e.code === 'Space' && !this.isRecording && e.target.tagName !== 'INPUT' && e.target.tagName !== 'TEXTAREA') {
                e.preventDefault();
                this.startVoiceInput();
            }
        });
        
        document.addEventListener('keyup', (e) => {
            if (e.code === 'Space' && this.isRecording) {
                e.preventDefault();
                this.stopVoiceInput();
            }
        });
    }

    // 切换语音输入状态（麦克风按钮）
    toggleVoiceInput() {
        if (this.isRecording) {
            this.stopVoiceInput();
            // 更新按钮状态
            const micBtn = document.getElementById('micBtn');
            if (micBtn) {
                micBtn.classList.remove('recording');
            }
        } else {
            this.startVoiceInput();
            // 更新按钮状态
            const micBtn = document.getElementById('micBtn');
            if (micBtn) {
                micBtn.classList.add('recording');
            }
        }
    }

    // 参考index.html的录音实现
    async startVoiceInput() {
        if (this.isRecording) return;
        if (!this.speechRecognitionSupported) {
            this.showError('当前浏览器不支持语音识别，请使用 Chrome 或改用文字输入');
            return;
        }
        
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            
            this.audioChunks = [];
            this.mediaRecorder = new MediaRecorder(stream);
            
            this.mediaRecorder.ondataavailable = (e) => {
                if (e.data.size > 0) {
                    this.audioChunks.push(e.data);
                }
            };
            
            this.mediaRecorder.onstop = () => {
                stream.getTracks().forEach(track => track.stop());
            };
            
            this.mediaRecorder.start();
            this.isRecording = true;
            
            // 更新麦克风按钮状态
            const micBtn = document.getElementById('micBtn');
            if (micBtn) {
                micBtn.classList.add('recording');
            }
            
            // 显示录音提示
            this.showSubtitle('正在录音，松开发送...');
            
            // 启动语音识别
            if (this.recognition) {
                try {
                    this.recognition.start();
                } catch (error) {
                    console.error('语音识别启动失败:', error);
                }
            }
            
        } catch (error) {
            console.error('无法访问麦克风:', error);
            if (error && error.name === 'NotAllowedError') {
                this.showError('麦克风权限被拒绝，请在浏览器设置中允许访问');
            } else if (error && error.name === 'NotFoundError') {
                this.showError('未检测到可用麦克风，请连接设备后重试');
            } else {
                this.showError('无法访问麦克风，请检查浏览器权限设置');
            }
        }
    }

    stopVoiceInput() {
        if (!this.isRecording) return;
        
        this.isRecording = false;
        
        // 更新麦克风按钮状态
        const micBtn = document.getElementById('micBtn');
        if (micBtn) {
            micBtn.classList.remove('recording');
        }
        
        // 停止录音
        if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
            this.mediaRecorder.stop();
        }
        
        // 停止语音识别
        if (this.recognition) {
            try {
                this.recognition.stop();
            } catch (error) {
                console.error('停止语音识别失败:', error);
            }
        }
    }

    showSubtitle(text) {
        // 只有在字幕开启时才显示
        if (this.subtitleEnabled) {
            this.subtitleOverlay.textContent = text;
            this.subtitleOverlay.classList.add('show');
        }
    }

    hideSubtitle() {
        // 清除定时器
        if (this.subtitleTimer) {
            clearTimeout(this.subtitleTimer);
            this.subtitleTimer = null;
        }
        
        // 延迟隐藏，确保过渡动画
        setTimeout(() => {
            this.subtitleOverlay.classList.remove('show');
        }, 100);
    }

    // 切换字幕显示状态
    toggleSubtitle() {
        this.subtitleEnabled = !this.subtitleEnabled;
        console.log('字幕状态:', this.subtitleEnabled ? '开启' : '关闭');
        
        // 如果关闭字幕，立即隐藏当前显示的字幕
        if (!this.subtitleEnabled) {
            this.subtitleOverlay.classList.remove('show');
        }
    }

    // 隐藏控制按钮（calling状态）
    hideControlButtons() {
        document.getElementById('subtitleBtn').classList.add('hidden');
        document.getElementById('micBtn').classList.add('hidden');
        document.getElementById('chatToggleBtn').classList.add('hidden');
    }

    // 显示控制按钮（接通后）
    showControlButtons() {
        document.getElementById('subtitleBtn').classList.remove('hidden');
        document.getElementById('micBtn').classList.remove('hidden');
        document.getElementById('chatToggleBtn').classList.remove('hidden');
    }

    hideLoading() {
        this.loadingOverlay.classList.add('hidden');
        if (this.retryConnectionBtn) {
            this.retryConnectionBtn.style.display = 'none';
            this.retryConnectionBtn.disabled = false;
        }
    }

    formatConnectionError(error) {
        if (!error) return '操作失败，请重试';
        switch (error.code) {
            case 'session_limit_reached':
                return '当前会话已满，请稍后重试';
            case 'request_timeout':
                return '服务响应超时，请检查后端状态后重试';
            case 'network_error':
                return '无法连接到后端，请检查服务地址和网络';
            case 'session_not_found':
                return '会话已结束，请重新连接';
            default:
                return error.message || '操作失败，请重试';
        }
    }

    showConnectionError(message) {
        const loadingText = document.getElementById('loadingText');
        this.loadingOverlay.classList.remove('hidden');
        if (loadingText) loadingText.textContent = '连接失败';
        this.updateLoadingProgress(message);
        if (this.retryConnectionBtn) {
            this.retryConnectionBtn.style.display = 'inline-flex';
            this.retryConnectionBtn.disabled = false;
        }
        this.hideControlButtons();
    }

    async retryConnection() {
        if (this.retryConnectionBtn) {
            this.retryConnectionBtn.disabled = true;
        }
        this.disconnect();
        const loadingText = document.getElementById('loadingText');
        if (loadingText) loadingText.textContent = '正在重新连接';
        this.updateLoadingProgress('正在准备数字人...');
        try {
            await this.connect();
        } catch (error) {
            console.error('重新连接失败:', error);
            this.showConnectionError(this.formatConnectionError(error));
        }
    }

    showError(message) {
        console.error(message);
        if (!this.errorBanner) return;
        if (this.errorTimer) {
            clearTimeout(this.errorTimer);
        }
        this.errorBanner.textContent = message;
        this.errorBanner.classList.add('show');
        this.errorTimer = setTimeout(() => {
            this.errorBanner.classList.remove('show');
            this.errorTimer = null;
        }, 4000);
    }

    /**
     * 使用 requestVideoFrameCallback 监控视频帧渲染
     * 检测丢帧和渲染延迟
     */
    setupVideoFrameCallback() {
        let lastFrameTime = 0;
        let freezeCount = 0;
        let totalFrames = 0;

        const onFrame = (now, metadata) => {
            totalFrames++;
            if (lastFrameTime > 0) {
                const delta = now - lastFrameTime;
                // 如果两帧间隔超过 80ms（正常应为 ~40ms@25fps），记为卡顿
                if (delta > 80) {
                    freezeCount++;
                    if (freezeCount % 10 === 1) {
                        console.warn(`视频卡顿检测: 帧间隔 ${delta.toFixed(0)}ms, 累计卡顿 ${freezeCount}/${totalFrames} 帧`);
                    }
                }
            }
            lastFrameTime = now;

            // 持续回调
            if (this.remoteVideo && !this.remoteVideo.paused) {
                this.remoteVideo.requestVideoFrameCallback(onFrame);
            }
        };

        this.remoteVideo.requestVideoFrameCallback(onFrame);
    }

    /**
     * 启动 WebRTC 连接质量监控
     * 每 3 秒采集一次 stats，检测丢包、jitter、帧率等
     */
    startStatsMonitor() {
        if (this._statsTimer) return;

        let prevVideoStats = null;
        let prevAudioStats = null;

        this._statsTimer = setInterval(async () => {
            if (!this.pc || this.pc.connectionState !== 'connected') return;

            try {
                const stats = await this.pc.getStats();
                stats.forEach(report => {
                    if (report.type === 'inbound-rtp' && report.kind === 'video') {
                        if (prevVideoStats) {
                            const timeDiff = (report.timestamp - prevVideoStats.timestamp) / 1000;
                            if (timeDiff <= 0) return;

                            const framesDecoded = report.framesDecoded - prevVideoStats.framesDecoded;
                            const framesDropped = report.framesDropped - prevVideoStats.framesDropped;
                            const packetsLost = report.packetsLost - prevVideoStats.packetsLost;
                            const fps = framesDecoded / timeDiff;
                            const jitter = report.jitter || 0;

                            // 只在有异常时打日志
                            if (fps < 20 || framesDropped > 0 || packetsLost > 0 || jitter > 0.05) {
                                console.warn('视频质量:', {
                                    fps: fps.toFixed(1),
                                    framesDropped,
                                    packetsLost,
                                    jitter: (jitter * 1000).toFixed(1) + 'ms',
                                    nackCount: report.nackCount,
                                    pliCount: report.pliCount
                                });
                            }
                        }
                        prevVideoStats = {
                            timestamp: report.timestamp,
                            framesDecoded: report.framesDecoded,
                            framesDropped: report.framesDropped,
                            packetsLost: report.packetsLost
                        };
                    }

                    if (report.type === 'inbound-rtp' && report.kind === 'audio') {
                        if (prevAudioStats) {
                            const packetsLost = report.packetsLost - prevAudioStats.packetsLost;
                            const jitter = report.jitter || 0;
                            if (packetsLost > 0 || jitter > 0.03) {
                                console.warn('音频质量:', {
                                    packetsLost,
                                    jitter: (jitter * 1000).toFixed(1) + 'ms',
                                    concealedSamples: report.concealedSamples
                                });
                            }
                        }
                        prevAudioStats = {
                            timestamp: report.timestamp,
                            packetsLost: report.packetsLost
                        };
                    }
                });
            } catch (e) {
                console.debug('WebRTC stats 获取失败:', e);
            }
        }, 3000);
    }

    stopStatsMonitor() {
        if (this._statsTimer) {
            clearInterval(this._statsTimer);
            this._statsTimer = null;
        }
    }

    disconnect() {
        this.stopStatsMonitor();
        this.stopVoiceInput();

        if (this.connectionRetryTimer) {
            clearTimeout(this.connectionRetryTimer);
            this.connectionRetryTimer = null;
        }

        if (this.mediaRecorder && this.mediaRecorder.stream) {
            this.mediaRecorder.stream.getTracks().forEach(track => track.stop());
        }
        this.mediaRecorder = null;

        if (this.recognition) {
            try {
                this.recognition.abort();
            } catch (error) {
                console.debug('语音识别清理失败:', error);
            }
        }

        if (this.dataChannel) {
            this.dataChannel.close();
            this.dataChannel = null;
        }

        if (this.pc) {
            this.pc.onconnectionstatechange = null;
            this.pc.close();
            this.pc = null;
        }

        if (this.remoteVideo) {
            this.remoteVideo.srcObject = null;
        }
        if (this.remoteAudio) {
            this.remoteAudio.srcObject = null;
        }

        if (this.subtitleTimer) {
            clearTimeout(this.subtitleTimer);
            this.subtitleTimer = null;
        }
        if (this.errorTimer) {
            clearTimeout(this.errorTimer);
            this.errorTimer = null;
            if (this.errorBanner) this.errorBanner.classList.remove('show');
        }

        this.sessionid = 0;
        this.isConnected = false;
    }
}

// 全局实例
let avatarClient;

// 立即初始化，不等待DOMContentLoaded
(function initClient() {
    // 检查DOM是否已加载
    if (document.readyState === 'loading') {
        // DOM未加载，等待DOMContentLoaded
        document.addEventListener('DOMContentLoaded', () => {
            console.log('DOMContentLoaded - 初始化客户端');
            avatarClient = new AvatarClient();
            window.avatarClient = avatarClient;
        });
    } else {
        // DOM已加载，立即初始化
        console.log('DOM已就绪 - 立即初始化客户端');
        avatarClient = new AvatarClient();
        window.avatarClient = avatarClient;
    }
})();

// 页面关闭时断开连接
window.addEventListener('pagehide', () => {
    if (avatarClient) avatarClient.disconnect();
});

window.addEventListener('pageshow', (event) => {
    if (event.persisted && avatarClient && !avatarClient.isConnected) {
        avatarClient.retryConnection();
    }
});
