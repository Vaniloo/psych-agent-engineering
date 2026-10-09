package com.vanilo.psych.agent.entity;

import jakarta.persistence.*;

import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Entity
@Table(name = "support_goals")
@Data
@NoArgsConstructor
public class SupportGoal {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(optional = false)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    @Version private Long version;

    @Column(nullable = false, length = 200)
    private String title;

    @Column(nullable = false, length = 1000)
    private String description = "";

    @Column(nullable = false, length = 20)
    private String status = "active";

    @Column(nullable = false, columnDefinition = "MEDIUMTEXT")
    private String actionsJson = "[]";

    @Column(nullable = false)
    private LocalDateTime updatedAt;
}
