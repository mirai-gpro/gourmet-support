import { defineConfig } from 'astro/config';

// https://astro.build/config
export default defineConfig({
  // Cloud Runで静的ファイルをホスティングする場合
  output: 'static',
  
  // ビルド設定
  build: {
    // アセットの出力先
    assets: 'assets'
  },
  
  // 開発サーバー設定
  server: {
    port: 4321,
    host: true
  },
  
  // Vite設定
  vite: {
    // 環境変数のプレフィックス
    envPrefix: 'PUBLIC_'
  }
});
