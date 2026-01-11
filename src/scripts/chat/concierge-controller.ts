// src/scripts/chat/concierge-controller.ts
import { CoreController } from './core-controller';
import { AudioManager } from './audio-manager';
// ★追加: 3Dアバターレンダラーのインポート
import { GVRM } from '../../gvrm-format/gvrm';

declare const io: any;

export class ConciergeController extends CoreController {
  
  // ★追加: GUAVA関連のプロパティ
  private guavaRenderer: GVRM | null = null;
  private analysisContext: AudioContext | null = null;
  private audioAnalyser: AnalyserNode | null = null;
  private analysisSource: MediaElementAudioSourceNode | null = null;
  private animationFrameId: number | null = null;

  constructor(container: HTMLElement, apiBase: string) {
    super(container, apiBase);
    
    // ★コンシェルジュモード用のAudioManagerを再初期化 (沈黙検知時間を長めに設定)
    this.audioManager = new AudioManager(8000);
    
    // コンシェルジュモードに設定
    this.currentMode = 'concierge';
    this.init();
  }

  // 初期化プロセスをオーバーライド
  protected async init() {
    // 親クラスの初期化を実行
    await super.init();
    
    // コンシェルジュ固有の要素とイベントを追加
    const query = (sel: string) => this.container.querySelector(sel) as HTMLElement;
    
    // ★修正: アバターコンテナの取得 (Concierge.astroの変更に対応)
    this.els.avatarContainer = query('#avatar3DContainer');
    this.els.modeSwitch = query('#modeSwitch') as HTMLInputElement;

    console.log('[ConciergeController] avatarContainer:', this.els.avatarContainer);
    console.log('[ConciergeController] this.container:', this.container);

    // ★追加: GUAVAレンダラーの初期化
    if (this.els.avatarContainer) {
      console.log('[ConciergeController] Creating GVRM with container:', this.els.avatarContainer.id);
      this.guavaRenderer = new GVRM(this.els.avatarContainer);
      
      try {
        // ★修正: 画像パスも正しく指定
        const success = await this.guavaRenderer.loadAssets('/assets/avatar_24p.ply', '/assets/source.png');
        
        if (success) {
          // 読み込み成功時: フォールバック画像を非表示に
          this.els.avatarContainer.classList.add('loaded');
          const fallback = document.getElementById('avatarFallback');
          if (fallback) fallback.style.display = 'none';
        } else {
          // 読み込み失敗時: フォールバック画像を表示
          console.warn('[GVRM] Asset loading failed, using fallback image');
          this.els.avatarContainer.classList.add('fallback');
        }
      } catch (error) {
        console.error('[GVRM] Initialization error:', error);
        this.els.avatarContainer.classList.add('fallback');
      }
    }

    // モードスイッチのイベントリスナー追加
    if (this.els.modeSwitch) {
      this.els.modeSwitch.addEventListener('change', () => {
        this.toggleMode();
      });
    }
  }

  // ========================================
  // 🎯 セッション初期化をオーバーライド
  // ========================================
  protected async initializeSession() {
    try {
      if (this.sessionId) {
        try {
          await fetch(`${this.apiBase}/api/session/end`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: this.sessionId })
          });
        } catch (e) {}
      }

      // 親クラスのgetUserIdを使用
      const userId = this.getUserId();

      const res = await fetch(`${this.apiBase}/api/session/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_info: { user_id: userId },
          language: this.currentLanguage,
          mode: 'concierge'
        })
      });
      const data = await res.json();
      this.sessionId = data.session_id;

      const greetingText = data.initial_message || this.t('initialGreetingConcierge');
      this.addMessage('assistant', greetingText, null, true);
      
      const ackTexts = [
        this.t('ackConfirm'), this.t('ackSearch'), this.t('ackUnderstood'), 
        this.t('ackYes'), this.t('ttsIntro')
      ];
      const langConfig = this.LANGUAGE_CODE_MAP[this.currentLanguage];
      
      const ackPromises = ackTexts.map(async (text) => {
        try {
          const ackResponse = await fetch(`${this.apiBase}/api/tts/synthesize`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
              text: text, language_code: langConfig.tts, voice_name: langConfig.voice 
            })
          });
          const ackData = await ackResponse.json();
          if (ackData.success && ackData.audio) {
            this.preGeneratedAcks.set(text, ackData.audio);
          }
        } catch (_e) { }
      });

      await Promise.all([
        this.speakTextGCP(greetingText), 
        ...ackPromises
      ]);
      
      this.els.userInput.disabled = false;
      this.els.sendBtn.disabled = false;
      this.els.micBtn.disabled = false;
      this.els.speakerBtn.disabled = false;
      this.els.speakerBtn.classList.remove('disabled');
      this.els.reservationBtn.classList.remove('visible');

    } catch (e) {
      console.error('[Session] Initialization error:', e);
    }
  }

  // ========================================
  // 🔧 Socket.IOの初期化をオーバーライド
  // ========================================
  protected initSocket() {
    // @ts-ignore
    this.socket = io(this.apiBase || window.location.origin);
    
    this.socket.on('connect', () => { });
    
    this.socket.on('transcript', (data: any) => {
      const { text, is_final } = data;
      if (this.isAISpeaking) return;
      if (is_final) {
        this.handleStreamingSTTComplete(text);
        this.currentAISpeech = "";
      } else {
        this.els.userInput.value = text;
      }
    });

    this.socket.on('error', (data: any) => {
      this.addMessage('system', `${this.t('sttError')} ${data.message}`);
      if (this.isRecording) this.stopStreamingSTT();
    });
  }

  // ========================================
  // 👄 GUAVA連携: 音声再生とリップシンク
  // ========================================
  
  // ★オーバーライド: 音声再生時にリップシンク解析を仕込む
  protected async speakTextGCP(text: string, stopPrevious: boolean = true, autoRestartMic: boolean = false, skipAudio: boolean = false) {
    if (skipAudio || !this.isTTSEnabled || !text) return Promise.resolve();

    if (stopPrevious) {
      this.stopCurrentAudio();
    }
    
    // ★GUAVA: リップシンク用のオーディオ解析をセットアップ
    this.setupAudioAnalysis();

    // ★GUAVA: 待機アニメーションなどを制御
    if (this.els.avatarContainer) {
      this.els.avatarContainer.classList.add('speaking');
    }
    
    // 親クラスのTTS処理を実行 (this.ttsPlayer.play() が呼ばれる)
    await super.speakTextGCP(text, stopPrevious, autoRestartMic, skipAudio);
    
    // 再生終了後
    this.stopAvatarAnimation();
  }

  // ★追加: 音声解析のセットアップ
  private setupAudioAnalysis() {
    if (!this.guavaRenderer) return;

    // AudioContextの作成（初回のみ）
    if (!this.analysisContext) {
      const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;
      this.analysisContext = new AudioContextClass();
    }

    // ユーザー操作後なのでresumeを試みる
    if (this.analysisContext.state === 'suspended') {
      this.analysisContext.resume().catch(e => console.log('AudioContext resume failed:', e));
    }

    // AnalyserNodeの作成
    if (!this.audioAnalyser) {
      this.audioAnalyser = this.analysisContext.createAnalyser();
      this.audioAnalyser.fftSize = 256; // サイズは調整可能
    }

    // MediaElementSourceの接続（初回のみ）
    if (!this.analysisSource && this.ttsPlayer) {
      try {
        
        this.analysisSource = this.analysisContext.createMediaElementSource(this.ttsPlayer);
        this.analysisSource.connect(this.audioAnalyser);
        this.audioAnalyser.connect(this.analysisContext.destination);
      } catch (e) {
        console.warn('MediaElementSource connection error:', e);
        // エラー時はリップシンク無効で再生だけ続ける
      }
    }

    // リップシンクループ開始
    this.startLipSyncLoop();
  }

  // ★追加: リップシンクループ
  private startLipSyncLoop() {
    if (this.animationFrameId) cancelAnimationFrame(this.animationFrameId);

    const update = () => {
      // 再生停止中または終了時は口を閉じる
      if (this.ttsPlayer.paused || this.ttsPlayer.ended) {
        this.guavaRenderer?.updateLipSync(0);
        
        if (this.ttsPlayer.ended) {
           this.animationFrameId = null;
           return; 
        }
      }

      if (this.audioAnalyser && this.guavaRenderer && !this.ttsPlayer.paused) {
        const dataArray = new Uint8Array(this.audioAnalyser.frequencyBinCount);
        this.audioAnalyser.getByteFrequencyData(dataArray);
        
        // 音量（振幅）の平均を計算
        let sum = 0;
        const range = dataArray.length; 
        for (let i = 0; i < range; i++) {
          sum += dataArray[i];
        }
        const average = sum / range;
        
        // 0.0 ~ 1.0 に正規化し、感度を調整
        const normalizedLevel = Math.min(1.0, (average / 255.0) * 2.5);

        this.guavaRenderer.updateLipSync(normalizedLevel);
      }
      
      this.animationFrameId = requestAnimationFrame(update);
    };

    this.animationFrameId = requestAnimationFrame(update);
  }

  // アバターアニメーション停止
  private stopAvatarAnimation() {
    if (this.els.avatarContainer) {
      this.els.avatarContainer.classList.remove('speaking');
    }
    // 口を閉じる
    this.guavaRenderer?.updateLipSync(0);
    if (this.animationFrameId) {
      cancelAnimationFrame(this.animationFrameId);
      this.animationFrameId = null;
    }
  }

  // ========================================
  // 🎯 UI言語更新をオーバーライド
  // ========================================
  protected updateUILanguage() {
    const initialMessage = this.els.chatArea.querySelector('.message.assistant[data-initial="true"] .message-text');
    const savedGreeting = initialMessage?.textContent;

    super.updateUILanguage();

    if (initialMessage && savedGreeting) {
      initialMessage.textContent = savedGreeting;
    }

    const pageTitle = document.getElementById('pageTitle');
    if (pageTitle) {
      pageTitle.innerHTML = `<img src="/pwa-152x152.png" alt="Logo" class="app-logo" /> ${this.t('pageTitleConcierge')}`;
    }
  }

  // モード切り替え処理 - ページ遷移
  private toggleMode() {
    const isChecked = this.els.modeSwitch?.checked;
    if (!isChecked) {
      console.log('[ConciergeController] Switching to Chat mode...');
      window.location.href = '/';
    }
  }

  // すべての活動を停止
  protected stopAllActivities() {
    super.stopAllActivities();
    this.stopAvatarAnimation();
  }

  // ========================================
  // 🎯 並行処理フロー: 応答を分割してTTS処理
  // ========================================
  private splitIntoSentences(text: string, language: string): string[] {
    let separator: RegExp;

    if (language === 'ja' || language === 'zh') {
      separator = /。/;
    } else {
      separator = /\.\s+/;
    }

    const sentences = text.split(separator).filter(s => s.trim().length > 0);

    return sentences.map((s, idx) => {
      if (idx < sentences.length - 1 || text.endsWith('。') || text.endsWith('. ')) {
        return language === 'ja' || language === 'zh' ? s + '。' : s + '. ';
      }
      return s;
    });
  }

  private async speakResponseInChunks(response: string, isTextInput: boolean = false) {
    if (isTextInput || !this.isTTSEnabled) {
      return this.speakTextGCP(response, true, false, isTextInput);
    }

    try {
      this.isAISpeaking = true;
      if (this.isRecording) {
        this.stopStreamingSTT();
      }
      
      // ★GUAVA: リップシンク準備
      this.setupAudioAnalysis();

      const sentences = this.splitIntoSentences(response, this.currentLanguage);

      if (sentences.length <= 1) {
        await this.speakTextGCP(response, true, false, isTextInput);
        this.isAISpeaking = false;
        return;
      }

      const firstSentence = sentences[0];
      const remainingSentences = sentences.slice(1).join('');
      const langConfig = this.LANGUAGE_CODE_MAP[this.currentLanguage];

      let firstSentenceAudioPromise: Promise<string | null> | null = null;
      let remainingAudioPromise: Promise<string | null> | null = null;

      if (this.isUserInteracted) {
        firstSentenceAudioPromise = (async () => {
          const cleanText = this.stripMarkdown(firstSentence);
          const response = await fetch(`${this.apiBase}/api/tts/synthesize`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              text: cleanText,
              language_code: langConfig.tts,
              voice_name: langConfig.voice
            })
          });
          const result = await response.json();
          return result.success ? `data:audio/mp3;base64,${result.audio}` : null;
        })();

        if (remainingSentences.trim().length > 0) {
          remainingAudioPromise = (async () => {
            const cleanText = this.stripMarkdown(remainingSentences);
            const response = await fetch(`${this.apiBase}/api/tts/synthesize`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                text: cleanText,
                language_code: langConfig.tts,
                voice_name: langConfig.voice
              })
            });
            const result = await response.json();
            return result.success ? `data:audio/mp3;base64,${result.audio}` : null;
          })();
        }

        if (firstSentenceAudioPromise) {
          const firstSentenceAudio = await firstSentenceAudioPromise;
          if (firstSentenceAudio) {
            const firstSentenceText = this.stripMarkdown(firstSentence);
            this.lastAISpeech = this.normalizeText(firstSentenceText);

            this.stopCurrentAudio();
            this.ttsPlayer.src = firstSentenceAudio;
            
            // ★GUAVA: 待機状態解除してリップシンク開始
            this.startLipSyncLoop();

            await new Promise<void>((resolve) => {
              this.ttsPlayer.onended = () => {
                this.els.voiceStatus.innerHTML = this.t('voiceStatusStopped');
                this.els.voiceStatus.className = 'voice-status stopped';
                resolve();
              };
              this.els.voiceStatus.innerHTML = this.t('voiceStatusSpeaking');
              this.els.voiceStatus.className = 'voice-status speaking';
              this.ttsPlayer.play();
            });

            if (remainingAudioPromise) {
              const remainingAudio = await remainingAudioPromise;
              if (remainingAudio) {
                const remainingText = this.stripMarkdown(remainingSentences);
                this.lastAISpeech = this.normalizeText(remainingText);

                await new Promise(r => setTimeout(r, 300));

                this.stopCurrentAudio();
                this.ttsPlayer.src = remainingAudio;
                
                // ★GUAVA: リップシンク継続
                this.startLipSyncLoop();

                await new Promise<void>((resolve) => {
                  this.ttsPlayer.onended = () => {
                    this.els.voiceStatus.innerHTML = this.t('voiceStatusStopped');
                    this.els.voiceStatus.className = 'voice-status stopped';
                    resolve();
                  };
                  this.els.voiceStatus.innerHTML = this.t('voiceStatusSpeaking');
                  this.els.voiceStatus.className = 'voice-status speaking';
                  this.ttsPlayer.play();
                });
              }
            }
          }
        }
      }
      
      this.stopAvatarAnimation();
      this.isAISpeaking = false;
    } catch (error) {
      console.error('[TTS並行処理エラー]', error);
      this.isAISpeaking = false;
      await this.speakTextGCP(response, true, false, isTextInput);
    }
  }

  // ========================================
  // 🎯 コンシェルジュモード専用: 音声入力完了時の即答処理
  // ========================================
  protected async handleStreamingSTTComplete(transcript: string) {
    this.stopStreamingSTT();
    
    if ('mediaSession' in navigator) {
      try { navigator.mediaSession.playbackState = 'playing'; } catch (e) {}
    }
    
    this.els.voiceStatus.innerHTML = this.t('voiceStatusComplete');
    this.els.voiceStatus.className = 'voice-status';

    const normTranscript = this.normalizeText(transcript);
    if (this.isSemanticEcho(normTranscript, this.lastAISpeech)) {
        this.els.voiceStatus.innerHTML = this.t('voiceStatusStopped');
        this.els.voiceStatus.className = 'voice-status stopped';
        this.lastAISpeech = '';
        return;
    }

    this.els.userInput.value = transcript;
    this.addMessage('user', transcript);
    
    const textLength = transcript.trim().replace(/\s+/g, '').length;
    if (textLength < 2) {
        const msg = this.t('shortMsgWarning');
        this.addMessage('assistant', msg);
        if (this.isTTSEnabled && this.isUserInteracted) {
          await this.speakTextGCP(msg, true);
        } else { 
          await new Promise(r => setTimeout(r, 2000)); 
        }
        this.els.userInput.value = '';
        this.els.voiceStatus.innerHTML = this.t('voiceStatusStopped');
        this.els.voiceStatus.className = 'voice-status stopped';
        return;
    }

    const ackText = this.t('ackYes');
    const preGeneratedAudio = this.preGeneratedAcks.get(ackText);
    
    let firstAckPromise: Promise<void> | null = null;
    if (preGeneratedAudio && this.isTTSEnabled && this.isUserInteracted) {
      firstAckPromise = new Promise<void>((resolve) => {
        // ★GUAVA: リップシンク準備
        this.setupAudioAnalysis();
        
        this.lastAISpeech = this.normalizeText(ackText);
        this.ttsPlayer.src = `data:audio/mp3;base64,${preGeneratedAudio}`;
        this.ttsPlayer.onended = () => resolve();
        this.ttsPlayer.play().catch(_e => resolve());
      });
    } else if (this.isTTSEnabled) { 
      firstAckPromise = this.speakTextGCP(ackText, false); 
    }
    
    this.addMessage('assistant', ackText);
    
    (async () => {
      try {
        if (firstAckPromise) await firstAckPromise;
        if (this.els.userInput.value.trim()) {
          this.isFromVoiceInput = true;
          this.sendMessage();
        }
      } catch (_error) {
        if (this.els.userInput.value.trim()) {
          this.isFromVoiceInput = true;
          this.sendMessage();
        }
      }
    })();
    
    this.els.voiceStatus.innerHTML = this.t('voiceStatusStopped');
    this.els.voiceStatus.className = 'voice-status stopped';
  }

  // ========================================
  // 🎯 コンシェルジュモード専用: メッセージ送信処理
  // ========================================
  protected async sendMessage() {
    let firstAckPromise: Promise<void> | null = null; 
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
      const textLength = message.trim().replace(/\s+/g, '').length;
      if (textLength < 2) {
           const msg = this.t('shortMsgWarning');
           this.addMessage('assistant', msg);
           if (this.isTTSEnabled && this.isUserInteracted) await this.speakTextGCP(msg, true);
           this.resetInputState();
           return;
      }
      
      this.els.userInput.value = '';
      
      const ackText = this.t('ackYes');
      this.currentAISpeech = ackText;
      this.addMessage('assistant', ackText);
      
      if (this.isTTSEnabled && !isTextInput) {
        try {
          const preGeneratedAudio = this.preGeneratedAcks.get(ackText);
          if (preGeneratedAudio && this.isUserInteracted) {
            firstAckPromise = new Promise<void>((resolve) => {
              // ★GUAVA: リップシンク準備
              this.setupAudioAnalysis();
              
              this.lastAISpeech = this.normalizeText(ackText);
              this.ttsPlayer.src = `data:audio/mp3;base64,${preGeneratedAudio}`;
              this.ttsPlayer.onended = () => resolve();
              this.ttsPlayer.play().catch(_e => resolve());
            });
          } else { 
            firstAckPromise = this.speakTextGCP(ackText, false); 
          }
        } catch (_e) {}
      }   
      if (firstAckPromise) await firstAckPromise;
    }

    this.isFromVoiceInput = false;
    
    if (this.waitOverlayTimer) clearTimeout(this.waitOverlayTimer);
    let responseReceived = false;
    
    this.waitOverlayTimer = window.setTimeout(() => { 
      if (!responseReceived) {
        this.showWaitOverlay(); 
      }
    }, 6500);

    try {
      const response = await fetch(`${this.apiBase}/api/chat`, { 
        method: 'POST', 
        headers: { 'Content-Type': 'application/json' }, 
        body: JSON.stringify({ 
          session_id: currentSessionId, 
          message: message, 
          stage: this.currentStage, 
          language: this.currentLanguage,
          mode: this.currentMode
        }) 
      });
      const data = await response.json();
      responseReceived = true;
      
      if (this.sessionId !== currentSessionId) return;
      
      if (this.waitOverlayTimer) {
        clearTimeout(this.waitOverlayTimer);
        this.waitOverlayTimer = null;
      }
      this.hideWaitOverlay();
      this.currentAISpeech = data.response;
      this.addMessage('assistant', data.response, data.summary);
      
      if (!isTextInput && this.isTTSEnabled) {
        this.stopCurrentAudio();
      }
      
      if (data.shops && data.shops.length > 0) {
        this.currentShops = data.shops;
        this.els.reservationBtn.classList.add('visible');
        this.els.userInput.value = '';
        document.dispatchEvent(new CustomEvent('displayShops', { 
          detail: { shops: data.shops, language: this.currentLanguage } 
        }));
        
        const section = document.getElementById('shopListSection');
        if (section) section.classList.add('has-shops');
        if (window.innerWidth < 1024) {
          setTimeout(() => {
            const shopSection = document.getElementById('shopListSection');
            if (shopSection) shopSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
           }, 300);
        }
        
        (async () => {
          try {
            this.isAISpeaking = true;
            if (this.isRecording) { this.stopStreamingSTT(); }

            await this.speakTextGCP(this.t('ttsIntro'), true, false, isTextInput);
            
            const lines = data.response.split('\n\n');
            let introText = ""; 
            let shopLines = lines;
            if (lines[0].includes('ご希望に合うお店') && lines[0].includes('ご紹介します')) { 
              introText = lines[0]; 
              shopLines = lines.slice(1); 
            }
            
            let introPart2Promise: Promise<void> | null = null;
            if (introText && this.isTTSEnabled && this.isUserInteracted && !isTextInput) {
                const preGeneratedIntro = this.preGeneratedAcks.get(introText);
              if (preGeneratedIntro) {
                introPart2Promise = new Promise<void>((resolve) => {
                  this.setupAudioAnalysis();
                  this.lastAISpeech = this.normalizeText(introText);
                  this.ttsPlayer.src = `data:audio/mp3;base64,${preGeneratedIntro}`;
                  this.ttsPlayer.onended = () => resolve();
                  this.ttsPlayer.play();
                });
              } else { 
                introPart2Promise = this.speakTextGCP(introText, false, false, isTextInput); 
              }
            }

            let firstShopAudioPromise: Promise<string | null> | null = null;
            let remainingAudioPromise: Promise<string | null> | null = null;
            const shopLangConfig = this.LANGUAGE_CODE_MAP[this.currentLanguage];
            
            if (shopLines.length > 0 && this.isTTSEnabled && this.isUserInteracted && !isTextInput) {
              const firstShop = shopLines[0];
              const restShops = shopLines.slice(1).join('\n\n');              
              firstShopAudioPromise = (async () => {
                const cleanText = this.stripMarkdown(firstShop);
                const response = await fetch(`${this.apiBase}/api/tts/synthesize`, { 
                  method: 'POST', 
                  headers: { 'Content-Type': 'application/json' }, 
                  body: JSON.stringify({ 
                    text: cleanText, language_code: shopLangConfig.tts, voice_name: shopLangConfig.voice 
                  }) 
                });
                const result = await response.json();
                return result.success ? `data:audio/mp3;base64,${result.audio}` : null;
              })();
              
              if (restShops) {
                remainingAudioPromise = (async () => {
                  const cleanText = this.stripMarkdown(restShops);
                  const response = await fetch(`${this.apiBase}/api/tts/synthesize`, { 
                    method: 'POST', 
                    headers: { 'Content-Type': 'application/json' }, 
                    body: JSON.stringify({ 
                      text: cleanText, language_code: shopLangConfig.tts, voice_name: shopLangConfig.voice 
                    }) 
                  });
                  const result = await response.json();
                  return result.success ? `data:audio/mp3;base64,${result.audio}` : null;
                })();
              }
            }

            if (introPart2Promise) await introPart2Promise;
            
            if (firstShopAudioPromise) {
              const firstShopAudio = await firstShopAudioPromise;
              if (firstShopAudio) {
                const firstShopText = this.stripMarkdown(shopLines[0]);
                this.lastAISpeech = this.normalizeText(firstShopText);
                
                if (!isTextInput && this.isTTSEnabled) {
                  this.stopCurrentAudio();
                }
                
                this.ttsPlayer.src = firstShopAudio;     
                this.setupAudioAnalysis();           
                await new Promise<void>((resolve) => { 
                  this.ttsPlayer.onended = () => { 
                    this.els.voiceStatus.innerHTML = this.t('voiceStatusStopped'); 
                    this.els.voiceStatus.className = 'voice-status stopped'; 
                    resolve(); 
                  }; 
                  this.els.voiceStatus.innerHTML = this.t('voiceStatusSpeaking'); 
                  this.els.voiceStatus.className = 'voice-status speaking'; 
                  this.ttsPlayer.play(); 
                });
                
                if (remainingAudioPromise) {
                  const remainingAudio = await remainingAudioPromise;
                  if (remainingAudio) {
                    const restShopsText = this.stripMarkdown(shopLines.slice(1).join('\n\n'));
                    this.lastAISpeech = this.normalizeText(restShopsText);
                    await new Promise(r => setTimeout(r, 500));
                    
                    if (!isTextInput && this.isTTSEnabled) {
                      this.stopCurrentAudio();
                    }
                    
                    this.ttsPlayer.src = remainingAudio;    
                    this.setupAudioAnalysis();                
                    await new Promise<void>((resolve) => { 
                      this.ttsPlayer.onended = () => { 
                        this.els.voiceStatus.innerHTML = '🎤 音声認識: 停止中'; 
                        this.els.voiceStatus.className = 'voice-status stopped'; 
                        resolve(); 
                      }; 
                      this.els.voiceStatus.innerHTML = '📊 音声再生中...'; 
                      this.els.voiceStatus.className = 'voice-status speaking'; 
                      this.ttsPlayer.play(); 
                    });
                  }
                }
              }
            }
            this.isAISpeaking = false;
            this.stopAvatarAnimation(); // 終了時に確実に止める
          } catch (_e) { 
            this.isAISpeaking = false; 
            this.stopAvatarAnimation();
          }
        })();
      } else {
        if (data.response) {
          const extractedShops = this.extractShopsFromResponse(data.response);
          if (extractedShops.length > 0) {
            this.currentShops = extractedShops;
            this.els.reservationBtn.classList.add('visible');
            document.dispatchEvent(new CustomEvent('displayShops', {
              detail: { shops: extractedShops, language: this.currentLanguage }
            }));
            const section = document.getElementById('shopListSection');
            if (section) section.classList.add('has-shops');
            this.speakResponseInChunks(data.response, isTextInput);
          } else {
            this.speakResponseInChunks(data.response, isTextInput);
          }
        }
      }
    } catch (error) { 
      console.error('送信エラー:', error);
      this.hideWaitOverlay(); 
      this.showError('メッセージの送信に失敗しました。'); 
    } finally { 
      this.resetInputState();
      this.els.userInput.blur();
    }
  }

}
