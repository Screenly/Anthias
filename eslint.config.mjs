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
    },
    rules: {
      'semi': ['error', 'always'],
      'quotes': ['error', 'single'],
      'indent': ['error', 2],
      'no-unused-vars': 'warn',
      'no-console': 'warn',
      'no-debugger': 'warn'
    }
  }
];