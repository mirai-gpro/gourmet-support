import { AudioManager } from './audio-manager';
// i18n のパスは環境に合わせて調整してください
import { i18n } from '../../constants/i18n'; 

declare const io: any;

export class ConciergeController {
  private container: HTMLElement;
  private apiBase: string;
  private audioManager: AudioManager;
  private socket: any = null;
  
  // コンシェルジュモード固定
  private currentMode = 'concierge';

  private currentLanguage: 'ja' | 'en' | 'zh' | 'ko' = 'ja';
  private sessionId: string | null = null;
  private isProcessing = false;
  private currentStage = 'conversation';
  private isRecording = false; 
  private waitOverlayTimer: number | null = null;
  private isTTSEnabled = true;
  private isUserInteracted = false;
  private currentShops: any[] = [];
  private isFromVoiceInput = false;
  private isAISpeaking = false;
  private currentAISpeech = "";
  
  private els: any = {};
  private ttsPlayer: HTMLAudioElement;

  private readonly LANGUAGE_CODE_MAP = {
    ja: { tts: 'ja-JP', stt: 'ja-JP', voice: 'ja-JP-Chirp3-HD-Leda' },
    en: { tts: 'en-US', stt: 'en-US', voice: 'en-US-Studio-O' },
    zh: { tts: 'cmn-CN', stt: 'cmn-CN', voice: 'cmn-CN-Wavenet-A' },
    ko: { tts: 'ko-KR', stt: 'ko-KR', voice: 'ko-KR-Wavenet-A' }
  };

  constructor(container: HTMLElement, apiBase: string) {
    this.container = container;
    this.apiBase = apiBase;
    this.audioManager = new AudioManager();
    this.ttsPlayer = new Audio();

    // DOM要素の取得
    const query = (sel: string) => container.querySelector(sel) as HTMLElement;
    this.els = {
      chatArea: query('#chatArea'),
      userInput: query('#userInput') as HTMLInputElement,
      sendBtn: query('#sendBtn'),
      micBtn: query('#micBtnFloat'),
      speakerBtn: query('#speakerBtnFloat'),
      voiceStatus: query('#voiceStatus'),
      waitOverlay: query('#waitOverlay'),
      waitVideo: query('#waitVideo') as HTMLVideoElement,
      splashOverlay: query('#splashOverlay'),
      splashVideo: query('#splashVideo') as HTMLVideoElement,
      reservationBtn: query('#reservationBtnFloat'),
      stopBtn: query('#stopBtn'),
      languageSelect: query('#languageSelect') as HTMLSelectElement,
      // 追加要素
      avatarContainer: query('.avatar-container'),
      shopListSection: query('#shopListSection'),
      shopCardList: query('#shopCardList')
    };

    this.init();
  }

  private async init() {
    this.bindEvents();
    this.initSocket();
    
    // スプラッシュ処理
    setTimeout(() => {
        if (this.els.splashVideo) this.els.splashVideo.loop = false;
        if (this.els.splashOverlay) {
             this.els.splashOverlay.classList.add('fade-out');
             setTimeout(() => this.els.splashOverlay.classList.add('hidden'), 800);
        }
    }, 2500);

    await this.initializeSession();
    this.updateUILanguage();
  }

  private bindEvents() {
    this.els.sendBtn?.addEventListener('click', () => this.sendMessage());
    
    this.els.micBtn?.addEventListener('click', () => {
      this.toggleRecording();
    });

    this.els.speakerBtn?.addEventListener('click', () => this.toggleTTS());
    
    this.els.stopBtn?.addEventListener('click', () => this.stopAllActivities());
    
    this.els.userInput?.addEventListener('keypress', (e: KeyboardEvent) => {
      if (e.key === 'Enter') this.sendMessage();
    });
    
    this.els.languageSelect?.addEventListener('change', () => {
      this.currentLanguage = this.els.languageSelect.value as any;
      this.updateUILanguage();
    });
  }

  private async sendMessage() {
    this.unlockAudioParams();
    const message = this.els.userInput.value.trim();
    if (!message || this.isProcessing) return;
    
    const currentSessionId = this.sessionId;
    const isTextInput = !this.isFromVoiceInput;
    
    this.isProcessing = true; 
    this.els.sendBtn.disabled = true;
    this.els.micBtn.disabled = true; 
    this.els.userInput.disabled = true;

    if (!this.isFromVoiceInput) {
      this.addMessage('user', message);
      this.els.userInput.value = '';
    }

    this.isFromVoiceInput = false;
    
    if (this.waitOverlayTimer) clearTimeout(this.waitOverlayTimer);
    this.waitOverlayTimer = window.setTimeout(() => { this.showWaitOverlay(); }, 4000);

    try {
      // ★バックエンドへ mode: 'concierge' を送信
      const response = await fetch(`${this.apiBase}/api/chat`, { 
        method: 'POST', 
        headers: { 'Content-Type': 'application/json' }, 
        body: JSON.stringify({ 
          session_id: currentSessionId, 
          message: message, 
          stage: this.currentStage, 
          language: this.currentLanguage,
          mode: 'concierge' 
        }) 
      });
      
      const data = await response.json();
      
      if (this.sessionId !== currentSessionId) return;
      
      this.hideWaitOverlay();
      this.currentAISpeech = data.response;
      this.addMessage('assistant', data.response);
      
      if (data.shops && data.shops.length > 0) {
        this.currentShops = data.shops;
        if (this.els.reservationBtn) this.els.reservationBtn.classList.add('visible');
        
        // ショップリスト表示イベント
        document.dispatchEvent(new CustomEvent('displayShops', { 
            detail: { shops: data.shops } 
        }));
      }

      if (this.isTTSEnabled) {
          await this.speakTextGCP(data.response, true, false, isTextInput);
      }

    } catch (error) { 
      console.error('Error:', error);
      this.hideWaitOverlay(); 
      this.addMessage('system', 'エラーが発生しました');
    } finally { 
      this.isProcessing = false;
      this.els.sendBtn.disabled = false;
      this.els.micBtn.disabled = false;
      this.els.userInput.disabled = false;
      this.els.userInput.blur();
    }
  }

  private async speakTextGCP(text: string, stopPrevious = true, autoRestartMic = false, skipAudio = false) {
    if (skipAudio || !this.isTTSEnabled || !text) return;

    if (stopPrevious) this.ttsPlayer.pause();
    
    // アバターアニメーションON
    if (this.els.avatarContainer) this.els.avatarContainer.classList.add('speaking');
    this.els.voiceStatus.innerHTML = "Speaking...";
    this.els.voiceStatus.className = 'voice-status speaking';

    try {
      const langConfig = this.LANGUAGE_CODE_MAP[this.currentLanguage];
      const response = await fetch(`${this.apiBase}/api/tts/synthesize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          text: text, language_code: langConfig.tts, voice_name: langConfig.voice 
        })
      });
      const data = await response.json();
      
      if (data.success && data.audio) {
        this.ttsPlayer.src = `data:audio/mp3;base64,${data.audio}`;
        
        await new Promise<void>((resolve) => {
          this.ttsPlayer.onended = () => {
             this.stopAvatarAnimation();
             resolve();
          };
          this.ttsPlayer.onerror = () => {
             this.stopAvatarAnimation();
             resolve();
          };
          
          if (this.isUserInteracted) {
             this.ttsPlayer.play().catch(() => {
                 this.stopAvatarAnimation();
                 resolve();
             });
          } else {
             this.stopAvatarAnimation();
             resolve();
          }
        });
        
        if (autoRestartMic && !this.isRecording) {
            this.toggleRecording();
        }
      }
    } catch (e) {
      console.error(e);
      this.stopAvatarAnimation();
    }
  }

  private stopAvatarAnimation() {
    if (this.els.avatarContainer) this.els.avatarContainer.classList.remove('speaking');
    this.els.voiceStatus.innerHTML = "Ready";
    this.els.voiceStatus.className = 'voice-status stopped';
  }

  private initSocket() {
    // @ts-ignore
    this.socket = io(this.apiBase || window.location.origin);
    this.socket.on('transcript', (data: any) => {
      if (data.is_final) {
        this.stopStreamingSTT();
        this.els.userInput.value = data.text;
        this.sendMessage();
      } else {
        this.els.userInput.value = data.text;
      }
    });
  }

  private async initializeSession() {
    try {
      const res = await fetch(`${this.apiBase}/api/session/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ language: this.currentLanguage })
      });
      const data = await res.json();
      this.sessionId = data.session_id;
      
      const greeting = this.t('initialGreeting');
      this.addMessage('assistant', greeting, null, true);
      this.speakTextGCP(greeting);
      
    } catch (e) { console.error(e); }
  }

  private async toggleRecording() {
    this.unlockAudioParams();
    if (this.isRecording) {
        this.stopStreamingSTT();
        return;
    }
    
    this.isRecording = true;
    this.els.micBtn.classList.add('recording');
    this.els.voiceStatus.innerHTML = "Listening...";
    this.els.voiceStatus.className = 'voice-status listening';
    
    const langCode = this.LANGUAGE_CODE_MAP[this.currentLanguage].stt;
    this.audioManager.startStreaming(this.socket, langCode, () => this.stopStreamingSTT());
  }

  private stopStreamingSTT() {
    this.audioManager.stopStreaming();
    if (this.socket) this.socket.emit('stop_stream');
    this.isRecording = false;
    this.els.micBtn.classList.remove('recording');
    this.els.voiceStatus.innerHTML = "Ready";
    this.els.voiceStatus.className = 'voice-status stopped';
  }

  private addMessage(role: string, text: string, summary: string | null = null, isInitial = false) {
    const div = document.createElement('div');
    div.className = `message ${role}`;
    const contentHtml = `<div class="message-content">${text}</div>`;
    div.innerHTML = `<div class="message-avatar">${role === 'assistant' ? '🍽' : '👤'}</div>${contentHtml}`;
    this.els.chatArea.appendChild(div);
    this.els.chatArea.scrollTop = this.els.chatArea.scrollHeight;
  }

  private showWaitOverlay() {
    if (this.els.waitOverlay) {
        this.els.waitOverlay.classList.remove('hidden');
        if (this.els.waitVideo) this.els.waitVideo.play().catch(() => {});
    }
  }

  private hideWaitOverlay() {
    if (this.waitOverlayTimer) clearTimeout(this.waitOverlayTimer);
    if (this.els.waitOverlay) {
        this.els.waitOverlay.classList.add('hidden');
        setTimeout(() => { if (this.els.waitVideo) this.els.waitVideo.pause(); }, 500);
    }
  }

  private stopAllActivities() {
      this.ttsPlayer.pause();
      this.stopStreamingSTT();
      this.hideWaitOverlay();
      this.stopAvatarAnimation();
  }

  private unlockAudioParams() {
    if (!this.isUserInteracted) {
        this.isUserInteracted = true;
        this.audioManager.unlockAudioParams(this.ttsPlayer);
    }
  }

  private toggleTTS() {
      this.isTTSEnabled = !this.isTTSEnabled;
      this.els.speakerBtn.classList.toggle('disabled', !this.isTTSEnabled);
  }

  private updateUILanguage() {
      this.els.userInput.placeholder = this.t('inputPlaceholder');
  }

  private t(key: string): string {
      // @ts-ignore
      const dict = i18n[this.currentLanguage];
      return dict ? (typeof dict[key] === 'string' ? dict[key] : key) : key;
  }
}