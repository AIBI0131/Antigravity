import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';
import { viteStaticCopy } from 'vite-plugin-static-copy';

export default defineConfig({
  plugins: [
    tailwindcss(),
    sveltekit(),
    // Copy ONNX Runtime WASM files to public/onnx/
    // Required for @huggingface/transformers WASM backend
    viteStaticCopy({
      targets: [
        {
          src: 'node_modules/onnxruntime-web/dist/*.wasm',
          dest: 'onnx'
        }
      ]
    })
  ],
  worker: {
    format: 'es'
  }
});
