// ESLint 扁平配置：TypeScript 推荐规则 + React hooks 规则；shadcn 生成目录与构建产物不检查
import js from '@eslint/js';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist', 'src/components/ui'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: { ecmaVersion: 2020, globals: globals.browser },
    plugins: { 'react-hooks': reactHooks, 'react-refresh': reactRefresh },
    rules: {
      // 只启用经典两条；插件新版 recommended 里的 React Compiler 系列规则会命中现有写法，暂不纳入
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      // 项目规范：strict mode 下不允许 any
      '@typescript-eslint/no-explicit-any': 'error',
    },
  },
);
