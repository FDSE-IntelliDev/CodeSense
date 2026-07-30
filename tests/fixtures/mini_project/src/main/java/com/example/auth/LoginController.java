package com.example.auth;

/** 处理登录请求的入口。用来验证 parser 能抽出 class / method / 调用关系。 */
public class LoginController {

    private final AuthService authService;

    public LoginController(AuthService authService) {
        this.authService = authService;
    }

    /** 登录入口：校验用户名密码并签发 token。 */
    public String login(String username, String password) {
        if (!authService.verifyCredentials(username, password)) {
            return null;
        }
        return authService.issueToken(username);
    }
}
