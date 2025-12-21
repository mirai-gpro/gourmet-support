// src/scripts/chat/concierge-controller.ts
import { CoreController } from './core-controller';

export class ConciergeController extends CoreController {
  
  constructor(container: HTMLElement, apiBase: string) {
    super(container, apiBase);
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
    this.els.avatarContainer = query('.avatar-container');
    this.els.avatarImage = query('#avatarImage') as HTMLImageElement;
    this.els.modeSwitch = query('#modeSwitch') as HTMLInputElement;
    
    // モードスイッチのイベントリスナー追加
    if (this.els.modeSwitch) {
      this.els.modeSwitch.addEventListener('change', () => {
        this.toggleMode();
      });
    }
  }

  // コンシェルジュモード固有: アバターアニメーション制御
  protected async speakTextGCP(text: string, stopPrevious: boolean = true, autoRestartMic: boolean = false, skipAudio: boolean = false) {
    if (skipAudio || !this.isTTSEnabled || !text) return Promise.resolve();

    if (stopPrevious) {
      this.ttsPlayer.pause();
    }
    
    // アバターアニメーションを開始
    if (this.els.avatarContainer) {
      this.els.avatarContainer.classList.add('speaking');
    }
    
    // 親クラスのTTS処理を実行
    await super.speakTextGCP(text, stopPrevious, autoRestartMic, skipAudio);
    
    // アバターアニメーションを停止
    this.stopAvatarAnimation();
  }

  // アバターアニメーション停止
  private stopAvatarAnimation() {
    if (this.els.avatarContainer) {
      this.els.avatarContainer.classList.remove('speaking');
    }
  }

  // モード切り替え処理
  private toggleMode() {
    const isChecked = this.els.modeSwitch?.checked;
    if (isChecked) {
      // コンシェルジュモード
      this.container.classList.add('mode-concierge');
      this.currentMode = 'concierge';
    } else {
      // 通常チャットモード
      this.container.classList.remove('mode-concierge');
      this.currentMode = 'chat';
    }
  }

  // すべての活動を停止（アバターアニメーションも含む）
  protected stopAllActivities() {
    super.stopAllActivities();
    this.stopAvatarAnimation();
  }
}