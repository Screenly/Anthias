module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'jest-fixed-jsdom',
  roots: ['<rootDir>/static/src'],
  testMatch: ['**/*.test.ts', '**/*.test.tsx'],
  transform: {
    '^.+\\.tsx?$': ['ts-jest', {
      tsconfig: 'tsconfig.test.json'
    }],
    '^.+\\.(js|mjs)$': ['ts-jest', {
      tsconfig: 'tsconfig.test.json',
      useESM: true
    }],
  },
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/static/src/$1',
  },
  setupFilesAfterEnv: ['<rootDir>/static/src/setupTests.ts'],
  transformIgnorePatterns: [
    'node_modules/(?!(msw|until-async)/)'
  ],
};
