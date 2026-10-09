package com.vanilo.psych.agent.repository;

import com.vanilo.psych.agent.entity.User;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface UserRepository extends JpaRepository<User, Long> {
    @org.springframework.data.jpa.repository.Lock(
            jakarta.persistence.LockModeType.PESSIMISTIC_WRITE)
    @org.springframework.data.jpa.repository.Query(
            "select u from User u where u.username = :username")
    Optional<User> lockByUsername(
            @org.springframework.data.repository.query.Param("username") String username);

    Optional<User> findByUsername(String username);
}
