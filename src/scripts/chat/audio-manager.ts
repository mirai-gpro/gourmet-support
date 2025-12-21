// src/scripts/chat/audio-manager.ts

// ★重要: オリジナルにあった独自のBase64変換関数をそのまま復元
const b64chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
function fastArrayBufferToBase64(buffer: ArrayBuffer) {
    let binary = '';
    const bytes = new Uint8Array(buffer);
    const len = bytes.byteLength;
    for (let i = 0; i < len; i += 3) {
      const c1 = bytes[i];
      const c2 = bytes[i + 1];
      const c3 = bytes[i + 2];
      const enc1 = c1 >> 2;
      const enc2 = ((c1 & 3) << 4) | (c2 >> 4);
      const enc3 = ((c2 & 15) << 2) | (c3 >> 6);
      const enc4 = c3 & 63;
      binary += b64chars[enc1] + b64chars[enc2];
      if (Number.isNaN(c2)) { binary += '=='; } 
      else if (Number.isNaN(c3)) { binary += b64chars[enc3] + '='; } 
      else { binary += b64chars[enc3] + b64chars[enc4]; }
    }
    return binary;
}

export class AudioManager {
  private audioContext: AudioContext | null = null;
  private globalAudioContext: AudioContext | null = null;
  private audioWorkletNode: AudioWorkletNode | null = null;
  private mediaStream: MediaStream | null = null;
  private analyser: AnalyserNode | null = null;

  private mediaRecorder: MediaRecorder | null = null;
  private audioChunks: Blob[] = [];

  private vadCheckInterval: number | null = null;
  private silenceTimer: number | null = null;
  private hasSpoken = false;
  private recordingStartTime = 0;
  private recordingTimer: number | null = null;
  
  // ★追加: iOS用の状態管理
  private isRecordingActive = false;
  
  private readonly SILENCE_THRESHOLD = 35;
  private readonly SILENCE_DURATION = 2000;
  private readonly MIN_RECORDING_TIME = 3000;
  private readonly MAX_RECORDING_TIME = 55000;

  private isIOS = /iPhone|iPad|iPod/i.test(navigator.userAgent);

  constructor() {
  }

  public unlockAudioParams(elementToUnlock: HTMLAudioElement) {
    if (this.globalAudioContext && this.globalAudioContext.state === 'suspended') {
      this.globalAudioContext.resume();
    }
    if (this.audioContext && this.audioContext.state === 'suspended') {
      this.audioContext.resume();
    }
    
  // ★削除: Audio要素の play/pause は音楽プレーヤーと干渉するため実行しない
  // if (elementToUnlock) {
  //     elementToUnlock.play().then(() => {
  //         elementToUnlock.pause();
  //         elementToUnlock.currentTime = 0;
  //     }).catch(() => {});
  // }
  }

  public fullResetAudioResources() {
    this.stopStreaming(); 
    
    if (this.globalAudioContext && this.globalAudioContext.state !== 'closed') {
      this.globalAudioContext.close();
      this.globalAudioContext = null;
    }
    if (this.audioContext && this.audioContext.state !== 'closed') {
      this.audioContext.close();
      this.audioContext = null;
    }
    if (this.mediaStream) {
      this.mediaStream.getTracks().forEach(track => track.stop());
      this.mediaStream = null;
    }
  }

  private async getUserMediaSafe(constraints: MediaStreamConstraints): Promise<MediaStream> {
    if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
      return navigator.mediaDevices.getUserMedia(constraints);
    }
    // @ts-ignore
    const legacyGetUserMedia = navigator.getUserMedia || navigator.webkitGetUserMedia || navigator.mozGetUserMedia || navigator.msGetUserMedia;
    if (legacyGetUserMedia) {
      return new Promise((resolve, reject) => {
        legacyGetUserMedia.call(navigator, constraints, resolve, reject);
      });
    }
    throw new Error('マイク機能が見つかりません。HTTPS(鍵マーク)のURLでアクセスしているか確認してください。');
  }

  public async startStreaming(
    socket: any, 
    languageCode: string, 
    onStopCallback: () => void,
    onSpeechStart?: () => void 
  ) {
    if (this.isIOS) {
      await this.startStreaming_iOS(socket, languageCode, onStopCallback);
    } else {
      await this.startStreaming_Default(socket, languageCode, onStopCallback, onSpeechStart);
    }
  }

  public stopStreaming() {
    if (this.isIOS) {
      this.stopStreaming_iOS();
    } else {
      this.stopStreaming_Default();
    }
    if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
      this.mediaRecorder.stop();
    }
    this.mediaRecorder = null;
  }

  // --- iOS用実装 (停止処理改善版) ---
  private async startStreaming_iOS(socket: any, languageCode: string, onStopCallback: () => void) {
    try {
      // ★修正1: 録音中フラグをチェック
      if (this.isRecordingActive) {
        throw new Error('Already recording');
      }
      
      if (this.recordingTimer) { clearTimeout(this.recordingTimer); this.recordingTimer = null; }
      
      // Workletクリーンアップ
      if (this.audioWorkletNode) { 
        this.audioWorkletNode.port.onmessage = null;
        this.audioWorkletNode.disconnect(); 
        this.audioWorkletNode = null; 
      }

      // AudioContext作成
      if (!this.globalAudioContext) {
        // @ts-ignore
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        this.globalAudioContext = new AudioContextClass({ 
          latencyHint: 'interactive',
          sampleRate: 48000
        });
      }
      
      if (this.globalAudioContext.state === 'suspended') {
        await this.globalAudioContext.resume();
      }

      // ★修正2: MediaStreamの状態を厳密にチェック
      const audioConstraints = { 
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          sampleRate: 48000
      };
      
      let needNewStream = false;
      
      if (this.mediaStream) {
        const tracks = this.mediaStream.getAudioTracks();
        // ★修正: より厳密な判定
        if (tracks.length === 0 || 
            tracks[0].readyState !== 'live' || 
            !tracks[0].enabled ||
            tracks[0].muted) {
          needNewStream = true;
        }
      } else {
        needNewStream = true;
      }
      
      if (needNewStream) {
        if (this.mediaStream) {
          this.mediaStream.getTracks().forEach(track => track.stop());
          this.mediaStream = null;
        }
        this.mediaStream = await this.getUserMediaSafe({ audio: audioConstraints });
      }
      
      const targetSampleRate = 16000;
      const nativeSampleRate = this.globalAudioContext.sampleRate;
      const downsampleRatio = nativeSampleRate / targetSampleRate;
      
      const source = this.globalAudioContext.createMediaStreamSource(this.mediaStream);
      const processorName = 'audio-processor-ios-' + Date.now(); 

      // Workletコード
      const audioProcessorCode = `
      class AudioProcessor extends AudioWorkletProcessor {
        constructor() {
          super();
          this.bufferSize = 8192;
          this.buffer = new Int16Array(this.bufferSize); 
          this.writeIndex = 0;
          this.ratio = ${downsampleRatio}; 
          this.inputSampleCount = 0;
          this.lastFlushTime = Date.now();
        }
        process(inputs, outputs, parameters) {
          const input = inputs[0];
          if (!input || input.length === 0) return true;
          const channelData = input[0];
          if (!channelData || channelData.length === 0) return true;
          for (let i = 0; i < channelData.length; i++) {
            this.inputSampleCount++;
            if (this.inputSampleCount >= this.ratio) {
              this.inputSampleCount -= this.ratio;
              if (this.writeIndex < this.bufferSize) {
                const s = Math.max(-1, Math.min(1, channelData[i]));
                const int16Value = s < 0 ? s * 0x8000 : s * 0x7FFF;
                this.buffer[this.writeIndex++] = int16Value;
              }
              if (this.writeIndex >= this.bufferSize || 
                  (this.writeIndex > 0 && Date.now() - this.lastFlushTime > 500)) {
                this.flush();
              }
            }
          }
          return true;
        }
        flush() {
          if (this.writeIndex === 0) return;
          const chunk = this.buffer.slice(0, this.writeIndex);
          this.port.postMessage({ audioChunk: chunk }, [chunk.buffer]);
          this.writeIndex = 0;
          this.lastFlushTime = Date.now();
        }
      }
      registerProcessor('${processorName}', AudioProcessor);
      `;

      const blob = new Blob([audioProcessorCode], { type: 'application/javascript' });
      const processorUrl = URL.createObjectURL(blob);
      await this.globalAudioContext.audioWorklet.addModule(processorUrl);
      URL.revokeObjectURL(processorUrl);
      
      this.audioWorkletNode = new AudioWorkletNode(this.globalAudioContext, processorName);
      this.audioWorkletNode.port.onmessage = (event) => {
        const { audioChunk } = event.data;
        if (socket && socket.connected && this.isRecordingActive) {
          try {
            const base64 = fastArrayBufferToBase64(audioChunk.buffer);
            socket.emit('audio_chunk', { chunk: base64, sample_rate: 16000 });
          } catch (e) { }
        }
      };
      
      // Socket送信準備
      if (socket && socket.connected) {
        socket.emit('stop_stream');
        await new Promise(resolve => setTimeout(resolve, 300));
      }
      
      socket.emit('start_stream', { 
        language_code: languageCode,
        sample_rate: 16000
      });

      // 接続
      source.connect(this.audioWorkletNode);
      this.audioWorkletNode.connect(this.globalAudioContext.destination);
      
      // ★修正3: 録音中フラグをON
      this.isRecordingActive = true;
      
      this.recordingTimer = window.setTimeout(() => { 
        this.stopStreaming_iOS();
        onStopCallback();
      }, this.MAX_RECORDING_TIME);

    } catch (error) {
      // ★修正4: エラー時にフラグをリセット
      this.isRecordingActive = false;
      if (this.audioWorkletNode) { 
        this.audioWorkletNode.port.onmessage = null;
        this.audioWorkletNode.disconnect(); 
        this.audioWorkletNode = null; 
      }
      throw error;
    }
  }

  private stopStreaming_iOS() {
    // ★修正5: 録音中フラグをOFF
    this.isRecordingActive = false;
    
    if (this.recordingTimer) { clearTimeout(this.recordingTimer); this.recordingTimer = null; }
    
    // ★修正6: Workletを完全に切断してから破棄
    if (this.audioWorkletNode) {
      try {
        this.audioWorkletNode.port.onmessage = null;
        this.audioWorkletNode.disconnect();
      } catch (e) {
        // 切断エラーは無視
      }
      this.audioWorkletNode = null;
    }
    
    // ★修正7: MediaStreamの状態を確認してから処理
    if (this.mediaStream) {
      const tracks = this.mediaStream.getAudioTracks();
      // トラックが無効な場合のみ停止
      if (tracks.length === 0 || tracks[0].readyState === 'ended') {
        this.mediaStream.getTracks().forEach(track => track.stop());
        this.mediaStream = null;
      }
      // それ以外は再利用のため残す
    }
  }

  // --- PC / Android用実装 ---
  private async startStreaming_Default(
    socket: any, 
    languageCode: string, 
    onStopCallback: () => void,
    onSpeechStart?: () => void
  ) {
    try {
      if (this.recordingTimer) { clearTimeout(this.recordingTimer); this.recordingTimer = null; }
      
      if (this.audioWorkletNode) { 
        this.audioWorkletNode.port.onmessage = null; 
        this.audioWorkletNode.disconnect(); 
        this.audioWorkletNode = null; 
      }
      
      if (!this.audioContext) {
        // @ts-ignore
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        this.audioContext = new AudioContextClass({ latencyHint: 'playback' });
      }
      
      if (this.audioContext!.state === 'suspended') {
        await this.audioContext!.resume();
      }
      
      if (this.mediaStream) { 
        this.mediaStream.getTracks().forEach(track => track.stop()); 
        this.mediaStream = null; 
      }
      
      const audioConstraints = { 
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true 
      };

      this.mediaStream = await this.getUserMediaSafe({ audio: audioConstraints });
      
      const targetSampleRate = 16000;
      const nativeSampleRate = this.audioContext!.sampleRate;
      const downsampleRatio = nativeSampleRate / targetSampleRate;
      
      const source = this.audioContext!.createMediaStreamSource(this.mediaStream);
      
      const audioProcessorCode = `
      class AudioProcessor extends AudioWorkletProcessor {
        constructor() {
          super();
          this.bufferSize = 16000;
          this.buffer = new Int16Array(this.bufferSize); 
          this.writeIndex = 0;
          this.ratio = ${downsampleRatio}; 
          this.inputSampleCount = 0;
          this.flushThreshold = 8000;
        }
        process(inputs, outputs, parameters) {
          const input = inputs[0];
          if (!input || input.length === 0) return true;
          const channelData = input[0];
          if (!channelData || channelData.length === 0) return true;
          for (let i = 0; i < channelData.length; i++) {
            this.inputSampleCount++;
            if (this.inputSampleCount >= this.ratio) {
              this.inputSampleCount -= this.ratio;
              if (this.writeIndex < this.bufferSize) {
                const s = Math.max(-1, Math.min(1, channelData[i]));
                const int16Value = s < 0 ? s * 0x8000 : s * 0x7FFF;
                this.buffer[this.writeIndex++] = int16Value;
              }
              if (this.writeIndex >= this.bufferSize) {
                this.flush();
              }
            }
          }
          return true;
        }
        flush() {
          if (this.writeIndex === 0) return;
          const chunk = this.buffer.slice(0, this.writeIndex);
          this.port.postMessage({ audioChunk: chunk }, [chunk.buffer]);
          this.writeIndex = 0;
        }
      }
      registerProcessor('audio-processor', AudioProcessor);
      `;

      try {
        const blob = new Blob([audioProcessorCode], { type: 'application/javascript' });
        const processorUrl = URL.createObjectURL(blob);
        await this.audioContext!.audioWorklet.addModule(processorUrl);
        URL.revokeObjectURL(processorUrl);
      } catch (workletError) {
        throw new Error(`音声処理初期化エラー: ${(workletError as Error).message}`);
      }
      
      this.audioWorkletNode = new AudioWorkletNode(this.audioContext!, 'audio-processor');
      this.audioWorkletNode.port.onmessage = (event) => {
        const { audioChunk } = event.data;
        if (socket && socket.connected) {
          try {
            const blob = new Blob([audioChunk], { type: 'application/octet-stream' });
            const reader = new FileReader();
            reader.onload = () => {
                const result = reader.result as string;
                const base64 = result.split(',')[1];
                socket.emit('audio_chunk', { chunk: base64, sample_rate: 16000 });
            };
            reader.readAsDataURL(blob);
          } catch (e) { }
        }
      };
      
      source.connect(this.audioWorkletNode);
      this.audioWorkletNode.connect(this.audioContext!.destination);

      this.analyser = this.audioContext!.createAnalyser();
      this.analyser.fftSize = 512;
      source.connect(this.analyser);
      const dataArray = new Uint8Array(this.analyser.frequencyBinCount);
      this.hasSpoken = false;
      this.recordingStartTime = Date.now();
      
      this.vadCheckInterval = window.setInterval(() => {
        if (!this.analyser) return;
        if (Date.now() - this.recordingStartTime < this.MIN_RECORDING_TIME) return;
        this.analyser.getByteFrequencyData(dataArray);
        const average = dataArray.reduce((a, b) => a + b, 0) / dataArray.length;
        
        if (average > this.SILENCE_THRESHOLD) { 
           this.hasSpoken = true; 
           if (this.silenceTimer) clearTimeout(this.silenceTimer);
           if (onSpeechStart) onSpeechStart(); 
        } else if (this.hasSpoken && !this.silenceTimer) { 
           this.silenceTimer = window.setTimeout(() => { 
             this.stopStreaming_Default();
             onStopCallback();
           }, this.SILENCE_DURATION); 
        }
      }, 100);

      if (socket && socket.connected) {
        socket.emit('stop_stream');
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      
      socket.emit('start_stream', { 
        language_code: languageCode,
        sample_rate: 16000
      });

      this.recordingTimer = window.setTimeout(() => { 
        this.stopStreaming_Default();
        onStopCallback();
      }, this.MAX_RECORDING_TIME);

    } catch (error) {
      if (this.mediaStream) { 
        this.mediaStream.getTracks().forEach(track => track.stop()); 
        this.mediaStream = null; 
      }
      throw error;
    }
  }

  private stopVAD_Default() {
      if (this.vadCheckInterval) { clearInterval(this.vadCheckInterval); this.vadCheckInterval = null; }
      if (this.silenceTimer) { clearTimeout(this.silenceTimer); this.silenceTimer = null; }
      if (this.analyser) { this.analyser = null; }
      if (this.audioContext && this.audioContext.state !== 'closed') { 
        this.audioContext.close(); 
        this.audioContext = null; 
      }
  }

  private stopStreaming_Default() {
    this.stopVAD_Default();
    if (this.recordingTimer) { clearTimeout(this.recordingTimer); this.recordingTimer = null; }
    
    if (this.audioWorkletNode) { 
      this.audioWorkletNode.port.onmessage = null; 
      this.audioWorkletNode.disconnect(); 
      this.audioWorkletNode = null; 
    }
    if (this.mediaStream) {
      this.mediaStream.getTracks().forEach(track => track.stop());
      this.mediaStream = null;
    }
    this.hasSpoken = false;
  }

  // --- レガシー録音 ---
  public async startLegacyRecording(
    onStopCallback: (audioBlob: Blob) => void,
    onSpeechStart?: () => void
  ) {
    try {
      if (this.recordingTimer) { clearTimeout(this.recordingTimer); this.recordingTimer = null; }

      const stream = await this.getUserMediaSafe({ 
        audio: { 
          channelCount: 1, 
          sampleRate: 16000, 
          echoCancellation: true, 
          noiseSuppression: true 
        } 
      });
      this.mediaStream = stream;

      // @ts-ignore
      this.mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' });
      this.audioChunks = [];
      this.hasSpoken = false;
      this.recordingStartTime = Date.now();

      // @ts-ignore
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      // @ts-ignore
      this.audioContext = new AudioContextClass();
      
      const source = this.audioContext!.createMediaStreamSource(stream);
      this.analyser = this.audioContext!.createAnalyser();
      this.analyser.fftSize = 512;
      source.connect(this.analyser);
      const dataArray = new Uint8Array(this.analyser.frequencyBinCount);

      this.vadCheckInterval = window.setInterval(() => {
        if (!this.analyser) return;
        if (Date.now() - this.recordingStartTime < this.MIN_RECORDING_TIME) return;
        
        this.analyser.getByteFrequencyData(dataArray);
        const average = dataArray.reduce((a, b) => a + b, 0) / dataArray.length;
        
        if (average > this.SILENCE_THRESHOLD) { 
           this.hasSpoken = true; 
           if (this.silenceTimer) clearTimeout(this.silenceTimer);
           if (onSpeechStart) onSpeechStart(); 
        } else if (this.hasSpoken && !this.silenceTimer) { 
           this.silenceTimer = window.setTimeout(() => { 
             if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
                this.mediaRecorder.stop();
             }
           }, this.SILENCE_DURATION); 
        }
      }, 100);

      // @ts-ignore
      this.mediaRecorder.ondataavailable = (event) => { 
        if (event.data.size > 0) this.audioChunks.push(event.data); 
      };

      // @ts-ignore
      this.mediaRecorder.onstop = async () => {
        this.stopVAD_Default();
        stream.getTracks().forEach(track => track.stop());
        if (this.recordingTimer) clearTimeout(this.recordingTimer);
        
        if (this.audioChunks.length > 0) {
           const audioBlob = new Blob(this.audioChunks, { type: 'audio/webm' });
           onStopCallback(audioBlob);
        }
      };

      // @ts-ignore
      this.mediaRecorder.start();

      this.recordingTimer = window.setTimeout(() => {
        if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
          this.mediaRecorder.stop();
        }
      }, this.MAX_RECORDING_TIME);

    } catch (error) {
      throw error;
    }
  }

  public async playTTS(_audioBase64: string): Promise<void> {
    return Promise.resolve();
  }

  public stopTTS() {}
}
