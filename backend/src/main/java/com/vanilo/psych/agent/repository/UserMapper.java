package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.entity.User;
import java.util.List;
import java.util.Optional;
import org.apache.ibatis.annotations.*;

@Mapper
public interface UserMapper {
    @Select("SELECT * FROM users WHERE username = #{username}")
    Optional<User> findByUsername(String username);

    @Select("SELECT * FROM users WHERE username = #{username} FOR UPDATE")
    Optional<User> lockByUsername(String username);

    @Select("SELECT * FROM users")
    List<User> findAll();

    @Select("SELECT COUNT(*) FROM users")
    long count();

    @Insert("""
            INSERT INTO users
                (username, password, role)
            VALUES
                (#{username}, #{password}, #{role})
            """)
    @Options(useGeneratedKeys = true, keyProperty = "id")
    void insert(User row);

}
