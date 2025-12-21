// src/scripts/chat/chat-controller.ts
import { CoreController } from './core-controller';

export class ChatController extends CoreController {
  
  constructor(container: HTMLElement, apiBase: string) {
    super(container, apiBase);
    this.init();
  }

  // 初期化プロセスは Core のものをそのまま利用
  // 追加のイベントや設定があればここで bindEvents などをオーバーライドするが、
  // 現状は Core と全く同じで良いため呼び出すだけ
  
  protected async init() {
    await super.init();
    // 安定版固有の初期化があればここに記述
  }
}