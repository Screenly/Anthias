import reactPlugin from 'eslint-plugin-react';
import tseslint from '@typescript-eslint/eslint-plugin';
import tsparser from '@typescript-eslint/parser';

export default [
  {
    ignores: [
      'dist/**',
      'static/dist/**',
      'static/spec/**',
      'static/js/backbone-0.9.10.min.js',
      'static/js/moment.js',
      'static/js/underscore-1.4.3.min.js',
      'static/js/base64js.min.js',
      'static/js/bootstrap.min.js',
      'static/js/bootstrap-datepicker.js',
      'static/js/bootstrap-timepicker.js',
      'static/js/popper.min.js',
      'static/js/jquery-3.7.1.min.js',
      'static/js/jquery-ui-1.10.1.custom.min.js',
      'static/js/jquery.fileupload.js',
      'static/js/jquery.iframe-transport.js',
    ],
  },
  {
    files: ['**/*.{js,mjs,cjs,jsx,mjsx,ts,tsx,mtsx}'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      parser: tsparser,
      parserOptions: {
        ecmaFeatures: {
          jsx: true
        },
        project: './tsconfig.json'
      }
    },
    plugins: {
      react: reactPlugin,
      '@typescript-eslint': tseslint
    },
    settings: {
      react: {
        version: 'detect'
      }
    },
    rules: {
      'semi': ['error', 'always'],
      'quotes': ['error', 'single'],
      'indent': 'off',
      'no-unused-vars': 'off',
      '@typescript-eslint/no-unused-vars': 'error',
      'no-console': 'error',
      'no-debugger': 'warn',
      'react/jsx-uses-react': 'error',
      'react/jsx-uses-vars': 'error',
      'react/jsx-no-duplicate-props': 'error',
      'react/jsx-key': 'warn',
      'react/jsx-max-props-per-line': ['warn', { maximum: 1, when: 'multiline' }],
      'react/jsx-first-prop-new-line': ['warn', 'multiline'],
      'react/jsx-closing-bracket-location': ['warn', 'line-aligned'],
      'react/jsx-tag-spacing': ['warn', {
        closingSlash: 'never',
        beforeSelfClosing: 'always',
        afterOpening: 'never',
        beforeClosing: 'never'
      }],
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/explicit-function-return-type': 'off',
      '@typescript-eslint/explicit-module-boundary-types': 'off',
      '@typescript-eslint/no-inferrable-types': 'error'
    }
  }
];
