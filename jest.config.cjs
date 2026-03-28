module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  roots: ['<rootDir>/tests', '<rootDir>/src'],
  collectCoverageFrom: ['forge-memory-client.ts', 'src/**/*.ts'],
  coveragePathIgnorePatterns: ['/node_modules/']
};
