module.exports = {
  "testEnvironment": "jsdom",
  "setupFilesAfterEnv": [
    "<rootDir>/jest.setup.ts"
  ],
  "moduleNameMapper": {
    "^@/(.*)$": "<rootDir>/$1",
    "\\.(css|less|scss|sass)$": "<rootDir>/__mocks__/styleMock.js"
  },
  "transform": {
    "^.+\\.(ts|tsx|js|jsx)$": [
      "babel-jest",
      {
        "presets": [
          [
            "@babel/preset-env",
            {
              "targets": {
                "node": "current"
              }
            }
          ],
          [
            "@babel/preset-react",
            {
              "runtime": "automatic"
            }
          ],
          "@babel/preset-typescript"
        ]
      }
    ]
  },
  "testMatch": [
    "**/__tests__/**/*.test.{ts,tsx}"
  ]
}