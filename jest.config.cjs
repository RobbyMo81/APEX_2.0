module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  roots: ['<rootDir>/tests'],
  testMatch: ['**/*.test.ts'],
  passWithNoTests: true,
  collectCoverageFrom: ['forge-memory-client.ts', 'src/**/*.ts'],
  coveragePathIgnorePatterns: ['/node_modules/']
};
