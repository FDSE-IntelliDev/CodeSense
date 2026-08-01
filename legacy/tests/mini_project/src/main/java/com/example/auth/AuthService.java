package com.example.auth;

/** 鉴权逻辑。故意和 LoginController 分开，好让调用图里有一条跨文件边。 */
public class AuthService {

    public boolean verifyCredentials(String username, String password) {
        return username != null && password != null && !password.isEmpty();
    }

    public String issueToken(String username) {
        return "token-" + username;
    }
}
