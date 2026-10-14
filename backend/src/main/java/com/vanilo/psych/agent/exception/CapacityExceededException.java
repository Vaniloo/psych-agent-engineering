package com.vanilo.psych.agent.exception;

public class CapacityExceededException extends ServiceUnavailableException {
    public CapacityExceededException() {
        super("当前对话请求较多，请稍后重试。你的消息还没有提交。");
    }
}
