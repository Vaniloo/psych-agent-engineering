package com.vanilo.psych.agent.entity;

import jakarta.persistence.*;
import lombok.Data;
import lombok.NoArgsConstructor;
import java.time.LocalDateTime;

@Entity
@Table(name="goal_check_ins", uniqueConstraints=@UniqueConstraint(columnNames={"user_id","source_request_id","action_id"}), indexes=@Index(name="idx_checkin_user_created",columnList="user_id,created_at"))
@Data @NoArgsConstructor
public class GoalCheckIn {
    @Id @GeneratedValue(strategy=GenerationType.IDENTITY) private Long id;
    @ManyToOne(optional=false) @JoinColumn(name="user_id",nullable=false) private User user;
    @ManyToOne(optional=false) @JoinColumn(name="goal_id",nullable=false) private SupportGoal goal;
    @Column(nullable=false,length=80) private String actionId;
    @Column(nullable=false,length=300) private String actionTitle;
    @Column(nullable=false,length=20) private String outcome;
    @Column(nullable=false,length=500) private String note="";
    @Column(nullable=false,length=20) private String source="manual";
    @Column(length=128) private String sourceRequestId;
    private Long sessionId;
    @Column(columnDefinition="TEXT") private String coachingJson;
    @Column(nullable=false,length=20) private String previousStatus;
    private Long appliedGoalVersion;
    @Column(nullable=false) private boolean feedbackAdded;
    @Column(nullable=false) private boolean undone;
    @Column(nullable=false) private LocalDateTime createdAt;
}
